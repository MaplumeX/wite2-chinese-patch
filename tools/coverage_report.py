#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
覆盖率权威报告 —— 以 Ghidra 字符串清单为准，不再用手写扫描器

旧口径的问题
------------
`coverage_sim.py` 用的是自己写的 UTF-16 扫描器，它在 `.rdata` 的
ASCII/UTF-16 混排区会错位（把 "South Pari**s**" 的尾巴粘到
"Create Depot Here ?" 前面），于是分母里混进假串、真串又可能漏。
那个数字（10128 条 / 52.2%）不可信。

现在的分母
----------
`build/rea/strings_authoritative.json` = Ghidra 已定义串 ∪ 严格对齐自扫描：
  - Ghidra 只列已定义的 string Data，`'Export to CSV?'` 这种未识别的不在内
  - 自扫描只接受前两字节为 `00 00` 的起点，杜绝 ASCII 尾巴粘连
两者合并去重后 12782 条，是目前能拿到的最干净的清单。

判定
----
    text 在翻译表里            → 覆盖（启动改写或绘制层至少一条路能到）
    text 不在表里且含英文单词   → 缺口
    text 不在表里且不含单词     → 数字/符号/布局，无需翻

用法:
    python tools/coverage_report.py
    python tools/coverage_report.py --gaps build/rea/gaps.csv
"""

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AUTH = os.path.join(ROOT, "build", "rea", "strings_authoritative.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

WORD = re.compile(r"[A-Za-z]{2,}")
LAYOUT = re.compile(r"^[\s~_\-]*$")

# 与 gap_triage.py 保持一致的用途分桶
BUCKETS = [
    ("log", re.compile(
        r"^(MEM |\*\*\*)|WARNING|ERROR|FAILED|unable to|relock|Realloc|"
        r"dealloc|assert|Could not|OPTDIAG", re.I)),
    ("editor_input", re.compile(
        r"^(Enter|Input|Type|Select this|Click to set|Choose|Eneter)\b", re.I)),
    ("editor_check", re.compile(
        r"(Unit ID %d|City ID %d|mismatch|should be \d|not an air base|"
        r"pilot number mismatch|in command:|duplicate)", re.I)),
    ("dialog", re.compile(r"~|^\w[\w ]*\?$")),
    ("path", re.compile(
        r"^[\\/]|[\\/]|\.(bmp|jpg|png|wav|txt|csv|dat|scn|sav|dll|exe)\b", re.I)),
    ("fmt", re.compile(r"%[-+ #0-9.]*[a-zA-Z]")),
    ("enum", re.compile(r"^[A-Z0-9_]{1,8}( [A-Z0-9_]{1,8})*$")),
    ("ident", re.compile(r"^[a-z][a-z0-9_ ]*$")),
]

UNESCAPE = {"t": "\t", "n": "\n", "\\": "\\"}


def unescape(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append(UNESCAPE.get(nxt, "\\" + nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def load_tsv(path):
    """按 dict_load 的规则读 TSV，返回 {小写 key: (原key, 译文, 作用域)}"""
    out = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line or line[0] == "#":
            continue
        esc = line[0] == "@"
        body = line[1:] if esc else line
        if "\t" not in body:
            continue
        key, rest = body.split("\t", 1)
        cols = rest.split("\t")
        val = cols[0]
        scope = cols[1] if len(cols) == 2 else ""
        if esc:
            key = unescape(key)
            val = unescape(val)
        out[key.lower()] = (key, val, scope)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gaps", help="导出缺口清单 CSV")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    if not os.path.exists(AUTH):
        sys.exit("找不到 %s\n先跑 tools/string_inventory.py" % AUTH)
    if not os.path.exists(TSV):
        sys.exit("找不到 %s" % TSV)

    auth = json.load(open(AUTH, encoding="utf-8"))
    tsv = load_tsv(TSV)
    print("权威字符串 %d 条 / 翻译表 %d 条" % (len(auth), len(tsv)))

    covered = 0
    gaps = {"needs": 0, "nolatin": 0}
    buckets = {}
    gap_rows = []

    for s in auth:
        t = s["text"]
        if t.lower() in tsv:
            covered += 1
            continue
        if LAYOUT.match(t) or not WORD.search(t):
            gaps["nolatin"] += 1
            continue
        gaps["needs"] += 1
        b = "other"
        for nm, pat in BUCKETS:
            if pat.search(t):
                b = nm
                break
        buckets[b] = buckets.get(b, 0) + 1
        gap_rows.append({"bucket": b, "text": t, "addr": s["addr"]})

    total = len(auth)
    print("\n覆盖 %d (%.1f%%)   缺口 %d (%.1f%%)   数字/布局 %d"
          % (covered, covered * 100.0 / total,
             gaps["needs"], gaps["needs"] * 100.0 / total,
             gaps["nolatin"]))

    if buckets:
        print("\n缺口按用途：")
        for b, n in sorted(buckets.items(), key=lambda kv: -kv[1]):
            print("  %-14s %5d" % (b, n))

    if args.show:
        for b in ("dialog", "other", "fmt"):
            sel = [r for r in gap_rows if r["bucket"] == b][:args.show]
            if sel:
                print("\n=== %s（前 %d）===" % (b, len(sel)))
                for r in sel:
                    print("  %r" % r["text"][:86])

    if args.gaps:
        with open(args.gaps, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["bucket", "text", "addr"])
            w.writeheader()
            w.writerows(gap_rows)
        print("\n缺口明细 -> %s" % args.gaps)


if __name__ == "__main__":
    main()
