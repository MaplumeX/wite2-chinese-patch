#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘点 Dat/CSV 里"给人看的文本列"

结论先行：部队名、将领名、地名、国家名全在这批 UTF-8 明文 CSV 里，
不在加密的 GenData/*.dat 里。exe 里有 `.\\Dat\\CSV\\` 路径和完整的 CSV
解析器（`ERROR unable to open %s`、`x%d (%d) %s in slot %d (new)`、
`City (%d) name change: %s -> %s`）。

本脚本统计每个 CSV 的每个列：是不是文本、有多少非空值、有多少重复、
最长多少字符 —— 用来判断哪些列能"一对一精准翻译"。

用法:
    python tools/csv_inventory.py
    python tools/csv_inventory.py --csv build/csv_inventory.csv
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

GAME = os.environ.get(
    "WITE2_GAME",
    r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2",
)
CSV_DIR = os.path.join(GAME, "Dat", "CSV")

# 用不上的列名（ID/数值/内部标记）
SKIP = re.compile(
    r"^(ID|id|\d+|X|Y|Color|Icon|Symbol|.*Store|.*Value|.*Mod|.*Modifier|"
    r".*Year|.*Month|.*Turn|Photo|InfoLink|Prim|Sub.*|BChain|BLink.*)$"
)


def is_text_column(vals):
    """非空值里，含字母的比例够高才算文本列"""
    nonempty = [v for v in vals if v.strip()]
    if not nonempty:
        return False, 0, 0
    alpha = 0
    for v in nonempty:
        if re.search(r"[A-Za-z]", v) and not re.fullmatch(r"-?\d+(\.\d+)?", v):
            alpha += 1
    return alpha >= max(1, len(nonempty) // 2), alpha, len(nonempty)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="导出完整报告到这个 CSV")
    args = ap.parse_args()

    if not os.path.isdir(CSV_DIR):
        sys.exit("找不到 %s（用 WITE2_GAME 环境变量指定游戏目录）" % CSV_DIR)

    files = []
    for root, _, names in os.walk(CSV_DIR):
        for n in names:
            if n.lower().endswith(".csv"):
                files.append(os.path.join(root, n))
    files.sort()

    print("Dat/CSV 下 %d 个 CSV\n" % len(files))
    print("%-24s %9s %-16s %7s %7s %5s" % (
        "文件", "大小", "文本列", "非空值", "唯一值", "最长"))
    print("-" * 76)

    report = []
    grand = Counter()
    for path in files:
        rel = os.path.relpath(path, CSV_DIR)
        try:
            # 这批 CSV 是 UTF-8 BOM 主体，但混了极少量 Latin-1
            # （Bátaszék / Kemijärvi / I° Gruppo Caccia）。surrogateescape
            # 把它们原样保住，不丢字节、不乱码。
            with open(path, encoding="utf-8-sig", errors="surrogateescape",
                      newline="") as f:
                rows = list(csv.reader(f))
        except Exception as e:
            print("%-24s 读取失败: %s" % (rel, e))
            continue
        if len(rows) < 2:
            continue
        header = rows[0]
        body = rows[1:]
        size = os.path.getsize(path)
        cols = []
        for ci, name in enumerate(header):
            if not name or SKIP.match(name):
                continue
            vals = [r[ci] for r in body if ci < len(r)]
            ok, nalpha, ntotal = is_text_column(vals)
            if not ok:
                continue
            uniq = len({v.strip() for v in vals if v.strip()})
            mx = max((len(v) for v in vals), default=0)
            cols.append((name, ntotal, uniq, mx))
            grand["text"] += ntotal
            grand["uniq"] += uniq
        if not cols:
            continue
        names = ",".join(c[0] for c in cols)
        tot_n = sum(c[1] for c in cols)
        tot_u = sum(c[2] for c in cols)
        tot_m = max(c[3] for c in cols)
        print("%-24s %9d %-22s %7d %7d %5d" % (
            rel, size, names[:22], tot_n, tot_u, tot_m))
        for name, n, u, mx in cols:
            report.append({
                "file": rel, "column": name,
                "nonempty": n, "unique": u, "maxlen": mx,
            })

    print("-" * 76)
    print("可翻文本行数合计 %d，去重后 %d" % (grand["text"], grand["uniq"]))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["file", "column", "nonempty", "unique", "maxlen"])
            w.writeheader()
            w.writerows(report)
        print("\n逐列明细 -> %s" % args.csv)


if __name__ == "__main__":
    main()
