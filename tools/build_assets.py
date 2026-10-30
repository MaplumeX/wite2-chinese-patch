#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成字体子集 + 翻译表

字体必须覆盖:
  1. zh_dict.json 里所有译文用到的汉字
  2. scen_trans.py 里剧本描述用到的汉字  <- 之前漏了这个

漏掉的后果: 剧本描述里 284 个汉字不在字体里, 显示成空白/方块,
整段读起来就是"缺字"和"语义不通"。

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
SRC_TTC = r"C:\Windows\Fonts\msyh.ttc"
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
