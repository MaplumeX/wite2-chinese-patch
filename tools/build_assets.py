#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成字体子集 + 翻译表

直接对微软雅黑做子集，不混用其他字体。

曾经试过以 Tahoma 为基底、只插入中文字形，想保留原始 ASCII 度量。
这个方向是错的: Tahoma 自带 TrueType hinting 程序（fpgm/prep/cvt），
应用到微软雅黑那些没有 hinting 的轮廓上会破坏渲染，表现为字形残缺
和漏字。混两种字体的 hinting 不可行。

字形覆盖:
  1. 完整 ASCII
  2. zh_dict.json 译文用字
  3. scen_trans.py 剧本描述用字
  4. GB2312 一级常用字 3755 —— 部队名/司令部名是运行时拼接的，
     拼出来的字可能不在词典里

注意: 中文译文比英文短，居中/右对齐的标签位置会随之变化。
这是翻译本身带来的，不是字体问题，调字体解决不了。

用法:
    python tools/build_assets.py           生成 TSV 和字体
    python tools/build_assets.py --check   只检查缺字
"""

import json
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DICT = os.path.join(ROOT, "zh_dict.json")
BLOCK = os.path.join(ROOT, "_block_names.txt")

# 这些是界面词条，不是部队番号名 —— 玩家在筛选框、类型下拉里见得到。
# 不允许进入封印名单，否则界面会出现半中半英。
GENERIC_KEEP = {
    "Cavalry", "Coastal", "Guards", "Motorized", "Mountain", "Mobile",
    "Reserve", "Lehr", "Frontier", "Middle", "North", "Southern",
    "Western", "Northwestern", "Southwestern", "Provisional", "Steppe",
    "Baltic", "Czech", "Finnish", "Hungarian", "Rumanian", "Slovakian",
    "Croatian", "Karelian", "Siberia", "Transbaikal", "Transcaucasus",
    "Urals", "Tatra", "Expeditionary", "Jewish",
}


def load_block_names():
    """读封印名单，并校验没把通用词条弄进去。"""
    if not os.path.exists(BLOCK):
        return []
    out = []
    with open(BLOCK, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    bad = [k for k in out if k in GENERIC_KEEP]
    if bad:
        raise SystemExit("封印名单里混进了通用词条：%s" % ", ".join(bad))
    return out
TSV = os.path.join(ROOT, "wite2_zh.tsv")
FONT_DIR = os.path.join(ROOT, "Dat", "Fonts")
FONT_FILE = "wite2_zh.ttf"
SRC_TTC = r"C:\Windows\Fonts\msyh.ttc"   # 中文字形来源
BASE_TTF = r"C:\Windows\Fonts\tahoma.ttf" # 仅取其垂直度量，不取字形
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


def short_class(k):
    """短串危险等级。

    短串（<=6 字符、无空格、纯 ASCII 标识符样）在 .rdata 里几乎都是
    枚举成员、ISO 语言码、CSV 列名或 BBCode 标记。全局改写会破坏
    游戏逻辑，而且是静默破坏 —— 界面上看不出来，只在特定操作时出错。

    实测撞到的例子：
        no    ISO 639 挪威语代码（ja|ko|nl|no|pl|pt|ro）
        de    德语代码
        CP    Command Points
        SP    西班牙国家码（AM|VC|IQ|SP|SZ|TU|DE）
        inf   CRT 浮点解析表（nINF|inf|INITY|NAN）
        st    序数后缀表（th|st|nd|rd），改了游戏会输出 "1圣"
        div   ISO 639 迪维希语代码
        url   BBCode 解析器 [url]...[/url]
        id,   CSV 表头

    这些串出现在界面上的概率很低，却一改就出事。绘制层能翻译它们，
    所以标记成 t（只绘制层），不动内存。
    """
    if " " in k or "	" in k:
        return 0
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-:'#]*", k):
        return 0
    return len(k) <= 6


def build_tsv():
    with open(DICT, encoding="utf-8") as f:
        raw = json.load(f)
    # _dataOnly: 这些键只改写 .data 段，不动 .rdata。
    # 工厂/资源类型名（PORT、RAILYARD、OIL…）在 .data 是定长显示数组，
    # 在 .rdata 另有同名枚举副本（PORT 的邻居是 AIRFIELD/UNIT/RAILWAY/
    # FERRY/INTERDICT，属任务类型枚举）。改枚举会让地图渲染出错。
    data_only = set(raw.get("_dataOnly", []))
    both = set(raw.get("_bothSections", []))
    # _rdataSafe: 确认安全的短串，允许照常改写两段。
    rdata_safe = set(raw.get("_rdataSafe", []))
    # _drawOnly: 明确只走绘制层的词条（"LOW RES" 这类多词全大写列头，
    # 在 .rdata 里可能是别的含义，改内存风险大于收益）
    draw_only = set(raw.get("_drawOnly", []))
    zh = {k: v for k, v in raw.items()
          if not k.startswith("_") and not str(v).startswith("----")}
    lines = ["# WITE2 中英对照表  UTF-8  TAB 分隔",
             "# 英文<TAB>中文[<TAB>d|r|b|t|x]  d=只改.data r=只改.rdata "
             "b=两段都改 t=只绘制层 x=封印(永不翻译)"]
    for k, v in zh.items():
        if k in draw_only:
            # 最优先。_drawOnly 曾经只是维护在 zh_dict.json 里、却从没
            # 参与过作用域计算 —— 标了等于没标，照样写进 .rdata。
            # Message 标了却还在写内存，就是 "通讯报告" 崩溃的原因。
            scope = "\tt"
        elif k in both:
            scope = "\tb"
        elif k in data_only:
            scope = "\td"
        elif short_class(k) and k not in rdata_safe:
            scope = "\tt"
        else:
            scope = ""
        lines.append("%s\t%s%s" % (k, v, scope))
    for k in load_block_names():
        # 译文列填原文本身：C 层命中后直接返回输入，不复制任何东西。
        # 填原文只是为了让三列结构保持一致。
        lines.append("%s\t%s\tx" % (k, k))
    print("封印 %d 条" % len(load_block_names()))
    with open(TSV, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    # selftest.exe 在 build/ 下运行，读的是相对路径 wite2_zh.tsv。
    # 不同步就会拿旧表跑测试 —— 曾经因此让 47 条已失效的用例“通过”，
    # 报告一片绿而 DLL 里早已是另一份词典。
    selftest_copy = os.path.join(os.path.dirname(TSV), "build", "wite2_zh.tsv")
    if os.path.isdir(os.path.dirname(selftest_copy)):
        import shutil
        shutil.copyfile(TSV, selftest_copy)
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
            if len(cols) == 2 and cols[1] not in (b"d", b"r", b"b", b"t"):
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
    """对微软雅黑做子集。

    不要改成"Tahoma 基底 + 插入中文字形"。试过，失败:
    Tahoma 自带 TrueType hinting（fpgm/prep/cvt），应用到微软雅黑
    那些没有 hinting 的轮廓上会破坏渲染，表现为字形残缺和漏字。
    两种字体的 hinting 不能混。
    """
    from fontTools.ttLib import TTCollection, TTFont
    from fontTools import subset

    coll = TTCollection(SRC_TTC, lazy=False)
    font = coll.fonts[0]
    cmap = font.getBestCmap()

    miss = sorted(c for c in chars if ord(c) not in cmap)
    print("需要字符 %d 个 (汉字 %d)"
          % (len(chars), len([c for c in chars if ord(c) > 0x7F])))
    if miss:
        print("源字体缺 %d 个: %s" % (len(miss), "".join(miss[:40])))
    else:
        print("源字体无缺字")

    if check_only:
        return

    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.drop_tables += ["DSIG"]
    sub = subset.Subsetter(options=opts)
    sub.populate(text="".join(sorted(chars)))
    sub.subset(font)

    # 垂直度量对齐 Tahoma。
    #
    # 游戏用 GetTextExtentPoint32W 实际测量文本，但行高取自字体的
    # hhea/OS-2 度量。微软雅黑行高 1.320 em，Tahoma 1.207 em，高 9.4%：
    #
    #   Tahoma    asc 2049  desc -423  -> 1.207 em
    #   雅黑      asc 2167  desc -536  -> 1.320 em
    #
    # 按 Tahoma 行高设计的框装不下，表现为文字偏下、超出框外、多行
    # 时行距不均。改成 Tahoma 的值后与原始布局一致。
    #
    # 安全性: 只改度量，不动字形轮廓和 hinting。实测中文字形范围
    # -0.172..0.847 em，Tahoma 的 desc -0.207 em 完全容纳，不会裁字。
    th = TTFont(BASE_TTF)
    font["hhea"].ascent = th["hhea"].ascent
    font["hhea"].descent = th["hhea"].descent
    font["hhea"].lineGap = th["hhea"].lineGap
    so, to = font["OS/2"], th["OS/2"]
    so.sTypoAscender = to.sTypoAscender
    so.sTypoDescender = to.sTypoDescender
    so.sTypoLineGap = to.sTypoLineGap
    so.usWinAscent = to.usWinAscent
    so.usWinDescent = to.usWinDescent
    print("垂直度量已对齐 Tahoma: asc=%d desc=%d"
          % (font["hhea"].ascent, font["hhea"].descent))

    nt = font["name"]
    for rec in list(nt.names):
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID == 4:
            rec.string = FAMILY + " Regular"
        elif rec.nameID == 6:
            rec.string = FAMILY + "-Regular"
        elif rec.nameID == 3:
            rec.string = FAMILY + ";v1.0"

    os.makedirs(FONT_DIR, exist_ok=True)
    out = os.path.join(FONT_DIR, FONT_FILE)
    font.save(out)
    print("字体 %d 字形, %d 字节 -> %s"
          % (len(TTFont(out).getBestCmap()), os.path.getsize(out),
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
