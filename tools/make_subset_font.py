#!/usr/bin/env python3
"""
WITE2 中文字体子集化工具

问题: Microsoft YaHei 含 29905 个码位(其中汉字 20992)。
      引擎用 GetFontUnicodeRanges 枚举字体声明的全部码位,逐个光栅化进纹理图集,
      47 个字体实例 × 上万字形 → 图集爆掉,加载卡死。

解法: 生成只含"实际用到的字符"的子集字体。
      cmap 只剩几百个码位 → GetFontUnicodeRanges 只返回这些 → 图集极小。

用法:
  python make_subset_font.py                 # 用 translation.csv 的字符 + ASCII
  python make_subset_font.py --all-common    # 额外加入常用汉字(备用,图集更大)
"""
import os, sys, csv, re, json, shutil
from fontTools import subset
from fontTools.ttLib import TTCollection

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
SRC_TTC = r"C:\Windows\Fonts\msyh.ttc"
FONT_OUT_DIR = os.path.join(GAME, "Dat", "Fonts")
OUT_NAME = "wite2_zh.ttf"

# 中文标点 + 常用符号(游戏 UI 里会出现的)
PUNCT = "，。、；：？！（）《》〈〉「」『』【】〔〕—…·～％＋－×÷＝　"
EXTRA = "年月日时分秒第号部队军团集团军师旅团营连排士兵弹药粮食物资运输铁路桥梁城市村镇地区气候温度冰雪雨雾泥泞干燥晴朗"


def collect_chars():
    chars = set()
    # 1) ASCII 可打印字符(英文原文仍在,必须保留)
    for cp in range(0x20, 0x7F):
        chars.add(chr(cp))
    # 2) 中文标点
    chars.update(PUNCT)
    # 3) 译文里的所有非 ASCII 字符
    trans = os.path.join(HERE, "translation.csv")
    if os.path.exists(trans):
        with open(trans, encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                if len(row) < 7:
                    continue
                for ch in row[6]:
                    if ord(ch) > 0x7F:
                        chars.add(ch)
    # 4) 备用常用字
    if "--all-common" in sys.argv:
        chars.update(EXTRA)
    return chars


def main():
    chars = collect_chars()
    zh = [c for c in chars if ord(c) > 0x7F]
    print("字符集: 共 %d 个 (其中非 ASCII %d 个)" % (len(chars), len(zh)))

    # 从 TTC 取第一个字体
    coll = TTCollection(SRC_TTC, lazy=False)
    font = coll.fonts[0]
    cmap = font.getBestCmap()
    missing = [c for c in chars if ord(c) not in cmap]
    if missing:
        print("警告: 源字体缺失 %d 个字符: %s" % (len(missing), "".join(missing[:30])))

    # 子集化
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.drop_tables += ["DSIG"]
    sub = subset.Subsetter(options=opts)
    sub.populate(text="".join(sorted(chars)))
    sub.subset(font)

    os.makedirs(FONT_OUT_DIR, exist_ok=True)
    out_path = os.path.join(FONT_OUT_DIR, OUT_NAME)
    font.save(out_path)

    # 验证结果
    check = TTCollection(out_path) if out_path.lower().endswith(".ttc") else None
    from fontTools.ttLib import TTFont
    f2 = TTFont(out_path)
    n = len(f2.getBestCmap())
    size = os.path.getsize(out_path)
    print("\n生成: %s" % out_path)
    print("  文件大小: %.1f KB" % (size / 1024))
    print("  码位数: %d  (原字体 29905, 减少 %.1f%%)" % (n, 100 * (1 - n / 29905)))
    print("  预计图集: 每个字体实例 %d 字形" % n)


if __name__ == "__main__":
    main()
