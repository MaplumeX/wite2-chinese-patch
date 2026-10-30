#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成字体子集 + 翻译表

字体以 Tahoma 为基底，只把中文字形插进去。

为什么不能直接换微软雅黑做基底: 游戏按固定像素布局，字宽变了位置就偏。
微软雅黑的 ASCII 字符比 Tahoma 宽很多，而且个别字符差异极大:

    M  +26.8%      A  +17.3%      W  +12.8%
    I  -21.2%      :  -31.9%      .  -20.5%
    平均 +4.4%

表现就是标签偏左偏右。用 Tahoma 做基底后，ASCII 度量与游戏原始布局
完全一致，只有中文字形是新增的（宽度 1.0 em，标准全宽）。

字形覆盖:
  1. 完整 ASCII（Tahoma 自带）
  2. zh_dict.json 译文用字
  3. scen_trans.py 剧本描述用字
  4. GB2312 一级常用字 3755 —— 部队名/司令部名是运行时拼接的，
     拼出来的字可能不在词典里

用法:
    python tools/build_assets.py           生成 TSV 和字体
    python tools/build_assets.py --check   只检查缺字
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DICT = os.path.join(ROOT, "zh_dict.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")
FONT_DIR = os.path.join(ROOT, "Dat", "Fonts")
FONT_FILE = "wite2_zh.ttf"
SRC_TTC = r"C:\Windows\Fonts\msyh.ttc"   # 中文字形来源
BASE_TTF = r"C:\Windows\Fonts\tahoma.ttf" # 基底: 保留原始 ASCII 度量
FAMILY = "WITE2ZH"
PUNCT = "，。：；！？、（）【】《》…—～·“”‘’"

sys.path.insert(0, ROOT)


def collect_chars():
    chars = {chr(c) for c in range(0x20, 0x7F)} | set(PUNCT)

    with open(DICT, encoding="utf-8") as f:
        d = json.load(f)
    for k, v in d.items():
        if k.startswith("_"):
            continue
        chars.update(c for c in v if ord(c) > 0x7F)

    # 剧本描述
    try:
        from scen_trans import TR
        for v in TR.values():
            chars.update(c for c in v if ord(c) > 0x7F)
    except ImportError:
        print("警告: 找不到 scen_trans.py, 剧本汉字不会进字体")

    # GB2312 一级常用字（3755 个）。
    #
    # 只收词典用字不够：部队名和司令部名是运行时拼接的
    # （数字 + 兵种缩写 + 编制层级），拼出来的字可能不在词典里。
    # 一级字库覆盖日常用字 99% 以上，体积也可控。
    gb = 0
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            chars.add(ch)
            gb += 1
    print("GB2312 汉字 %d 个已纳入" % gb)

    return chars


def build_tsv():
    with open(DICT, encoding="utf-8") as f:
        raw = json.load(f)
    # _dataOnly: 这些键只改写 .data 段，不动 .rdata。
    # 工厂/资源类型名（PORT、RAILYARD、OIL…）在 .data 是定长显示数组，
    # 在 .rdata 另有同名枚举副本（PORT 的邻居是 AIRFIELD/UNIT/RAILWAY/
    # FERRY/INTERDICT，属任务类型枚举）。改枚举会让地图渲染出错。
    data_only = set(raw.get("_dataOnly", []))
    zh = {k: v for k, v in raw.items()
          if not k.startswith("_") and not str(v).startswith("----")}
    lines = ["# WITE2 中英对照表  UTF-8  TAB 分隔",
             "# 英文<TAB>中文[<TAB>d]   d = 只改 .data 段"]
    for k, v in zh.items():
        lines.append("%s\t%s%s" % (k, v, "\td" if k in data_only else ""))
    with open(TSV, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("翻译表 %d 条 -> %s" % (len(zh), os.path.basename(TSV)))
    verify_tsv()


def verify_tsv():
    """回读 TSV，模拟 C 的 dict_load，检查列解析没漏。

    真实事故：dict_load 没剥第三列，译文带上了 "\\td"，
    界面上显示成「大雨d」。这种错误写词典时看不出来，
    只有实际跑游戏才会暴露，所以在这里拦。
    """
    bad = []
    n = 0
    with open(TSV, "rb") as f:
        for raw in f:
            line = raw.rstrip(b"\r\n")
            if not line or line.startswith(b"#"):
                continue
            if b"\t" not in line:
                continue
            n += 1
            en, rest = line.split(b"\t", 1)
            cols = rest.split(b"\t")
            if len(cols) > 2:
                bad.append((en.decode("utf-8", "replace"), "列数 %d" % len(cols)))
                continue
            zh = cols[0]
            if len(cols) == 2 and cols[1] not in (b"d", b"r"):
                bad.append((en.decode("utf-8", "replace"),
                            "作用域标记 %r" % cols[1]))
            if b"\t" in zh:
                bad.append((en.decode("utf-8", "replace"), "译文含 tab"))
    if bad:
        print("TSV 列解析异常 %d 条：" % len(bad))
        for en, why in bad[:10]:
            print("  %-40r %s" % (en, why))
    else:
        print("TSV 校验通过（%d 条，列结构正常）" % n)


def build_font(chars, check_only=False):
    """以 Tahoma 为基底，把中文字形插进去。

    Tahoma 只含拉丁字符，中文码位从微软雅黑取字形和轮廓。
    中文 advance 固定 1.0 em（2048），是标准全宽。

    ASCII 度量完全不动 —— 这是修复标签偏移的关键。
    """
    from fontTools.ttLib import TTFont
    import copy

    base = TTFont(BASE_TTF)
    src = TTFont(SRC_TTC, fontNumber=0)

    if base["head"].unitsPerEm != src["head"].unitsPerEm:
        raise SystemExit("unitsPerEm 不一致，无法直接合并")

    bcmap = base.getBestCmap()
    scmap = src.getBestCmap()
    bglyf = base["glyf"]
    sglyf = src["glyf"]
    order = base.getGlyphOrder()
    added = 0
    missing_src = []

    def ensure_glyph(cp):
        """把 cp 的字形（含复合字形依赖）复制到基底字体，返回字形名。"""
        nonlocal added
        name = "uni%04X" % cp
        if name in bglyf.glyphs:
            return name
        if cp not in scmap:
            return None
        sg = scmap[cp]
        g = sglyf[sg]
        # 复合字形：先把依赖的字形复制过来并重命名
        if g.isComposite():
            for comp in g.components:
                cname = comp.glyphName
                if cname in bglyf.glyphs:
                    continue
                try:
                    ccp = src.getReverseGlyphMap()[cname]
                except KeyError:
                    ccp = None
                if ccp is None:
                    continue
                ensure_glyph(ccp)
                if ("uni%04X" % ccp) in bglyf.glyphs:
                    comp.glyphName = "uni%04X" % ccp
        bglyf.glyphs[name] = copy.deepcopy(g)
        order.append(name)
        # 中文全宽 1.0 em；lsb 沿用源字体
        base["hmtx"][name] = (2048, src["hmtx"][sg][1])
        added += 1
        return name

    for ch in sorted(chars):
        cp = ord(ch)
        if cp < 0x80:
            continue                      # ASCII 用 Tahoma 自带
        if cp in bcmap:
            continue                      # Tahoma 已有
        nm = ensure_glyph(cp)
        if nm is None:
            missing_src.append(ch)
            continue
        for tbl in base["cmap"].tables:
            if tbl.isUnicode():
                tbl.cmap[cp] = nm

    print("需要字符 %d 个 (汉字 %d)"
          % (len(chars), len([c for c in chars if ord(c) > 0x7F])))
    print("从微软雅黑插入 %d 个字形" % added)
    if missing_src:
        print("源字体缺 %d 个: %s"
              % (len(missing_src), "".join(missing_src[:40])))
    else:
        print("源字体无缺字")

    if check_only:
        return

    # 删掉 hdmx：它缓存了每个字号的逐字符宽度，新增字形不在表里
    # 会让某些字号取到错误的度量，表现为间距不均匀。
    for t in ("hdmx", "LTSH", "VDMX", "DSIG"):
        if t in base:
            del base[t]

    nt = base["name"]
    for rec in list(nt.names):
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID == 4:
            rec.string = FAMILY + " Regular"
        elif rec.nameID == 6:
            rec.string = FAMILY + "-Regular"
        elif rec.nameID == 3:
            rec.string = FAMILY + ";v1.0"

    # 子集化: Tahoma 自带 4495 字形（含大量拉丁扩展、希腊、西里尔），
    # 我们只需要 ASCII + 中文。裁掉能省一半体积。
    from fontTools import subset as _sub
    keep = set(chars) | {chr(c) for c in range(0x20, 0x7F)}
    opts = _sub.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.drop_tables += ["DSIG"]
    subber = _sub.Subsetter(options=opts)
    subber.populate(text="".join(sorted(keep)))
    subber.subset(base)

    os.makedirs(FONT_DIR, exist_ok=True)
    out = os.path.join(FONT_DIR, FONT_FILE)
    base.save(out)
    from fontTools.ttLib import TTFont as _T
    print("字体 %d 字形, %d 字节 -> %s"
          % (len(_T(out).getBestCmap()), os.path.getsize(out),
             os.path.relpath(out, ROOT)))


def main():
    check_only = "--check" in sys.argv
    chars = collect_chars()
    if not check_only:
        build_tsv()
    build_font(chars, check_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
