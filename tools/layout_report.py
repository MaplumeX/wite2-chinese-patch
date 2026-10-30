# -*- coding: utf-8 -*-
"""分析布局诊断日志

游戏目录放一个空的 wite2_layout.ini，跑游戏，打开游戏选项界面，
会生成 wite2_layout.txt。用这个脚本对比「测量宽度」和「绘制坐标」。

关键判断:
  - 同一列的所有文本，DRAW x 是否相同
      相同 -> 左对齐，中文窄就是结束得早，不是坐标偏移
      不同 -> 居中对齐，x = 中心 - 宽度/2，中文窄则左边缘右移
  - MEASURE 的 cx 是否与字体度量吻合
      吻合 -> 游戏用的是实测宽度，没有缓存英文宽度

用法:
    python tools/layout_report.py              概要
    python tools/layout_report.py --col 200    只看 x 附近的条目
    python tools/layout_report.py --diff       和英文原文宽度对比
"""

import io
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
LOG = os.path.join(GAME, "wite2_layout.txt")
DICT = os.path.join(ROOT, "zh_dict.json")


def load():
    if not os.path.exists(LOG):
        print("找不到 %s" % LOG)
        print("先放一个空的 wite2_layout.ini 到游戏目录，跑游戏，"
              "打开游戏选项界面。")
        return None
    rows = []
    with io.open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            # DRAW     123    45  n= 12  text
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            kind, a, b = parts[0], parts[1], parts[2]
            rest = parts[3] + " " + parts[4]
            n = 0
            if rest.startswith("n="):
                try:
                    n = int(rest[2:].split()[0])
                except ValueError:
                    n = 0
                text = rest.split(None, 1)[1] if " " in rest else ""
            else:
                text = parts[4]
            try:
                rows.append((kind, int(a), int(b), n, text))
            except ValueError:
                continue
    return rows


def main():
    rows = load()
    if rows is None:
        return 1

    draws = [r for r in rows if r[0] == "DRAW"]
    meas = [r for r in rows if r[0] == "MEASURE"]
    print("记录: 绘制 %d 条, 测量 %d 条" % (len(draws), len(meas)))
    if not draws:
        print("没有绘制记录。诊断钩子可能没生效，看 wite2_hook.txt 有没有")
        print("  [layout] 诊断开启")
        return 1

    if "--col" in sys.argv:
        want = int(sys.argv[sys.argv.index("--col") + 1])
        sel = [r for r in draws if abs(r[1] - want) <= 6]
        print("\n=== x ≈ %d 的条目 (%d 条) ===" % (want, len(sel)))
        for _, x, y, n, t in sorted(sel, key=lambda r: r[2]):
            print("  y=%-4d x=%-4d n=%-3d %r" % (y, x, n, t))
        return 0

    # 按 x 聚簇: 左对齐的列 x 相同
    byx = defaultdict(list)
    for _, x, y, n, t in draws:
        byx[x].append((y, n, t))
    print("\n=== 绘制 x 坐标分布 ===")
    for x in sorted(byx, key=lambda v: -len(byx[v]))[:12]:
        ys = sorted(r[0] for r in byx[x])
        print("  x=%-5d %3d 条  y=%d..%d" % (x, len(byx[x]), ys[0], ys[-1]))

    # 关键判断: 同一列内 x 是否恒定
    print("\n=== 同一列内 x 是否恒定 ===")
    multi = [x for x in byx if len(byx[x]) >= 4]
    if multi:
        for x in sorted(multi, key=lambda v: -len(byx[v]))[:6]:
            widths = [r[1] for r in byx[x]]
            print("  x=%-5d %d 条, 宽度 %d..%d 字 -> x %s"
                  % (x, len(byx[x]), min(widths), max(widths),
                     "恒定（左对齐）" if len(set(
                         abs(v - x) for v in widths)) == 1 else "有浮动"))
    else:
        print("  没有足够长的列可判断")

    if "--diff" in sys.argv:
        with io.open(DICT, encoding="utf-8") as f:
            d = json.load(f)
        rev = {}
        for k, v in d.items():
            if k.startswith("_") or str(v).startswith("----"):
                continue
            rev.setdefault(v, k)
        print("\n=== 译文 vs 原文（字符数） ===")
        print("  %-28s %-28s %s" % ("译文", "原文", "差"))
        shown = 0
        for _, x, y, n, t in draws:
            if t not in rev or shown >= 20:
                continue
            en = rev[t]
            if en == t:
                continue
            print("  %-28r %-28r %+d" % (t, en, n - len(en)))
            shown += 1
        if shown == 0:
            print("  （绘制文本都直接匹配原文，说明替换没生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
