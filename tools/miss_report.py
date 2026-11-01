#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把运行时未翻译日志整理成可操作的缺口清单

数据来自 DLL 写的 `wite2_miss.tsv`（放一个空的 `wite2_miss.ini` 就开），
形式是 `次数<TAB>原文`。

为什么按次数排序就能分开两类东西
--------------------------------
事件正文是**逐词绘制**的：一句话被拆成几十个单词，每个词单独调一次
绘制。所以正文里的词每次只出现一两次。而界面标签（按钮、表头、状态
栏、单位面板字段）是每帧都画的，次数会迅速涨到几十上百。

所以不需要分类器：次数高的就是界面漏翻，次数低的多半是正文碎词。

用法:
    python tools/miss_report.py                    # 默认阈值
    python tools/miss_report.py --min-count 5      # 放宽
    python tools/miss_report.py --all              # 连正文碎词一起列
    python tools/miss_report.py --write-cand out.tsv
"""

import argparse
import collections
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# DLL 把日志写在**当前工作目录**（游戏目录），不是项目目录。
# 两个位置都找，先项目后游戏，便于把日志拷过来分析。
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
CANDIDATES = [os.path.join(ROOT, "wite2_miss.tsv"),
              os.path.join(GAME, "wite2_miss.tsv")]
ZH = os.path.join(ROOT, "zh_dict.json")

# 纯正文碎词的形状：单个小写词、无数字、无标点
PROSE = re.compile(r"^[a-z']{1,14}$")


def load_miss(path):
    rows = []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#") or "\t" not in line:
                continue
            c, t = line.rstrip("\n").split("\t", 1)
            t = t.strip()
            if not t:
                continue
            try:
                n = int(c)
            except ValueError:
                continue
            rows.append((n, t))
    return rows


def classify(text, count, min_count):
    if text in KNOWN_AMBIG:
        return "ambiguous"
    if PROSE.match(text):
        return "prose"
    if count >= min_count:
        return "ui"
    if len(text) <= 30 and (any(c.isupper() for c in text) or any(c.isdigit() for c in text)):
        return "ui"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=20)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--file", default=None,
                    help="指定日志路径（默认自动在项目目录/游戏目录里找）")
    ap.add_argument("--write-cand", default=None,
                    help="把 ui 类候选写成 TSV（原文<TAB>次数）")
    args = ap.parse_args()

    miss = args.file or next((p for p in CANDIDATES if os.path.isfile(p)), None)
    if not miss:
        print("没有找到 wite2_miss.tsv。\n"
              "在游戏目录放一个空的 wite2_miss.ini，进游戏把界面走一遍，"
              "退出后再跑本脚本。")
        for p in CANDIDATES:
            print("  找过: %s" % p)
        return 2

    global KNOWN_AMBIG
    KNOWN_AMBIG = set()
    if os.path.isfile(ZH):
        with io.open(ZH, encoding="utf-8") as f:
            z = json.load(f)
        KNOWN_AMBIG = set(z.get("_ambiguous", {}).keys()) if isinstance(
            z.get("_ambiguous"), dict) else set(z.get("_ambiguous") or [])

    rows = load_miss(miss)
    if not rows:
        print("日志是空的 —— 确认这次游戏运行时 wite2_miss.ini 存在")
        return 2

    bucket = collections.defaultdict(list)
    for n, t in rows:
        bucket[classify(t, n, args.min_count)].append((n, t))

    print("来源 %s" % miss)
    print("非重复 %d 条，绘制总次数 %d\n" % (len(rows), sum(n for n, _ in rows)))
    for k in ("ui", "other", "ambiguous", "prose"):
        print("  %-10s %4d 条" % (k, len(bucket[k])))

    ui = sorted(bucket["ui"], reverse=True)
    print("\n=== 界面类缺口（次数降序，前 %d）===" % args.top)
    if not ui:
        print("  （无）")
    for n, t in ui[:args.top]:
        print("  %5d  %s" % (n, t))

    if bucket["ambiguous"]:
        print("\n=== 已知歧义串（之前判定为控件/协议名，故意没翻）===")
        for n, t in sorted(bucket["ambiguous"], reverse=True)[:15]:
            print("  %5d  %s" % (n, t))

    if args.all:
        print("\n=== 其余（含正文碎词，前 %d）===" % args.top)
        rest = sorted(bucket["other"] + bucket["prose"], reverse=True)
        for n, t in rest[:args.top]:
            print("  %5d  %s" % (n, t))

    if args.write_cand:
        with io.open(args.write_cand, "w", encoding="utf-8", newline="\n") as f:
            f.write("# 运行时实测的界面缺口候选：次数\t原文\n")
            for n, t in ui:
                f.write("%d\t%s\n" % (n, t))
        print("\n候选已写入 %s（%d 条）" % (args.write_cand, len(ui)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
