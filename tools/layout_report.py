# -*- coding: utf-8 -*-
"""分析布局诊断日志

游戏目录放一个空的 wite2_layout.ini，跑游戏，打开游戏选项界面，
会生成 wite2_layout.txt。

数据来源: 0x45CE60 绘制函数的栈参数。游戏用自建字体图集 + D3D 渲染，
不走 GDI 的 ExtTextOutW，所以只能从这个函数拿坐标。

日志格式:
    DRAW a1=200 a2=350 a3=0 a5=0 n=12 战争迷雾

a1..a5 是 5 个栈参数的原始值。哪个是 x、哪个是 y 要看数值范围：
  - 屏幕宽度约 1280，y 一般 < 768
  - a3 常见小整数（对齐标志/颜色索引）
  - a5 可能是 flags

用法:
    python tools/layout_report.py            概要 + 参数含义推断
    python tools/layout_report.py --args     每个参数的取值范围
    python tools/layout_report.py --col a1   按某个参数分组看是否恒定
"""

import io
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
LOG = os.path.join(GAME, "wite2_layout.txt")
DICT = os.path.join(ROOT, "zh_dict.json")

LINE = re.compile(r"^DRAW\s+a1=(-?\d+)\s+a2=(-?\d+)\s+a3=(-?\d+)\s+"
                  r"a5=(-?\d+)\s+n=(\d+)\s+(.*)$")


def load():
    if not os.path.exists(LOG):
        print("找不到 %s" % LOG)
        return None
    rows = []
    with io.open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE.match(line.rstrip("\r\n"))
            if not m:
                continue
            rows.append((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                         int(m.group(4)), int(m.group(5)), m.group(6)))
    return rows


def main():
    rows = load()
    if rows is None:
        return 1
    print("记录 %d 条" % len(rows))
    if not rows:
        print("日志为空。确认 wite2_layout.ini 存在，且这次 DLL 已部署。")
        return 1

    names = ["a1", "a2", "a3", "a5"]
    vals = [[r[i] for r in rows] for i in range(4)]

    print("\n=== 各参数取值范围 ===")
    for nm, v in zip(names, vals):
        uniq = len(set(v))
        print("  %-3s  min=%-6d max=%-6d 不同值 %-4d  %s"
              % (nm, min(v), max(v), uniq,
                 "可能是 x（随文本变化）" if uniq > len(rows) * 0.5
                 else "恒定或少量取值"))

    # 哪个参数像 x: 值域在 0..1400，且随文本变化
    print("\n=== 候选坐标轴 ===")
    for nm, v in zip(names, vals):
        span = max(v) - min(v)
        if 100 < max(v) < 2000 and span > 50:
            print("  %-3s 值域 %d..%d，跨度 %d  -> 很可能是 x 或 y"
                  % (nm, min(v), max(v), span))

    if "--args" in sys.argv:
        print("\n=== 每条记录的原始参数 ===")
        for a1, a2, a3, a5, n, t in rows[:40]:
            print("  a1=%-6d a2=%-6d a3=%-4d a5=%-4d n=%-3d %r"
                  % (a1, a2, a3, a5, n, t))
        return 0

    which = "a1"
    if "--col" in sys.argv:
        which = sys.argv[sys.argv.index("--col") + 1]
    idx = names.index(which)

    print("\n=== 按 %s 分组（判断对齐方式） ===" % which)
    groups = defaultdict(list)
    for r in rows:
        groups[r[idx]].append(r)
    big = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]
    for key, g in big:
        ns = [r[4] for r in g]
        others = [r[1 - idx if idx < 2 else 0] for r in g]
        print("  %s=%-6d %2d 条  文本长度 %d..%d  另一轴 %d..%d"
              % (which, key, len(g), min(ns), max(ns),
                 min(others), max(others)))

    # 关键: 同一 x 的多条记录，长度不同 -> 左对齐则 x 相同
    print("\n=== 同一 %s 下长度差异很大的条目 ===" % which)
    for key, g in big:
        if len(g) < 3:
            continue
        ns = [r[4] for r in g]
        if max(ns) - min(ns) < 4:
            continue
        print("  %s=%d:" % (which, key))
        for r in sorted(g, key=lambda r: r[4])[:8]:
            print("      n=%-3d %r" % (r[4], r[5]))
        break

    if "--diff" in sys.argv:
        with io.open(DICT, encoding="utf-8") as f:
            d = json.load(f)
        rev = {}
        for k, v in d.items():
            if k.startswith("_") or str(v).startswith("----"):
                continue
            rev.setdefault(v, k)
        print("\n=== 译文 vs 原文 ===")
        shown = 0
        for a1, a2, a3, a5, n, t in rows:
            if t not in rev or shown >= 25:
                continue
            en = rev[t]
            if en == t:
                continue
            print("  n=%-3d %-24r <- %r" % (n, t, en))
            shown += 1
        if not shown:
            print("  （没有匹配，可能替换没生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
