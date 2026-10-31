"""按字体度量核对译文宽度，找出会溢出原位的条目。

界面列宽是按英文留的。英文平均字符约 0.56 em，中文汉字整 1.0 em，
所以：

  "Qty"  1.5 em -> "数量" 2.0 em    变宽 33%，列里会挤到右边
  "A FtrL" 5.7 em -> "攻方战斗机损失" 14 em   2.5 倍，必然溢出

这个工具把词典里每条按字体实际 advance 算宽度，列出 ratio 最高的。
`--short` 只看原文 ≤ 6 字符的短标签（列头、按钮文字，最容易出问题），
`--min` 调整阈值。

用法:
    python tools/fit_check.py                # 全部，ratio > 1.15
    python tools/fit_check.py --short        # 只看短标签
    python tools/fit_check.py --min 1.4      # 更严
    python tools/fit_check.py --csv out.csv  # 写 CSV
"""

import argparse
import csv
import json
import os
import sys

from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
FONT = os.path.join(ROOT, "Dat", "Fonts", "wite2_zh.ttf")


def load_metrics():
    f = TTFont(FONT)
    hmtx = f["hmtx"]
    cmap = f.getBestCmap()
    upm = f["head"].unitsPerEm

    def adv(ch):
        g = cmap.get(ord(ch))
        if g:
            return hmtx[g][0]
        return upm if ord(ch) > 0x2000 else 606

    return adv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=1.15, help="最小宽度比")
    ap.add_argument("--short", type=int, nargs="?", const=6, default=0,
                    help="只看原文不超过 N 字符（默认 6）")
    ap.add_argument("--csv", help="输出 CSV 路径")
    args = ap.parse_args()

    adv = load_metrics()

    def w(s):
        return sum(adv(c) for c in s)

    with open(DICT, encoding="utf-8") as f:
        d = json.load(f)

    rows = []
    for k, v in d.items():
        if k.startswith("_") or not isinstance(v, str) or v == "----":
            continue
        if args.short and len(k) > args.short:
            continue
        a, b = w(k), w(v)
        if a <= 0:
            continue
        r = b / a
        if r < args.min:
            continue
        rows.append((r, a, b, len(k), k, v))
    rows.sort(key=lambda x: (-x[0], x[4]))

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["ratio", "src_adv", "dst_adv", "src_len", "src", "dst"])
            for r, a, b, n, k, v in rows:
                wr.writerow(["%.3f" % r, a, b, n, k, v])
        print("%d 条 -> %s" % (len(rows), args.csv))
        return 0

    print("%d 条译文比原文宽（ratio >= %.2f）" % (len(rows), args.min))
    print("%-6s %-6s %-6s %-4s %s" % ("ratio", "src", "dst", "len", "条目"))
    for r, a, b, n, k, v in rows:
        print("%.2fx  %-6d %-6d %-4d %r -> %r" % (r, a, b, n, k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
