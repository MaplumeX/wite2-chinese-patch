#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
> **注意口径**：本脚本的输入是手写扫描器的结果（含假串）。
> 权威分桶请用 `tools/coverage_report.py`。这里保留是因为分桶规则
> 本身（log / editor_input / dialog / path / fmt …）在两边是同一套。

把 .rdata 里"未收录"的串按用途分诊，分离出真正的界面缺口

背景
----
`coverage_sim.py` 算出 4868 条 .rdata 文本没有词条，但其中一大部分
是编辑器日志、校验输出、font/MEM 诊断 —— 玩家根本看不到。
直接说"还有 4868 条要翻"是错的。

本脚本按**用途**分桶，只把"玩家真的会看到"的挑出来。

分类依据（都可核对，不是拍脑袋）：
    ~        游戏用它作换行，出现即多行对话框文字
    %d %s    格式串，说明有运行时插值
    ***      日志前缀
    Enter/Input/Select/Click   交互提示语

用法:
    python tools/gap_triage.py
    python tools/gap_triage.py --csv build/gap_triage.csv
    python tools/gap_triage.py --bucket dialog
"""

import argparse
import csv
import os
import re
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

WORD = re.compile(r"[A-Za-z]{2,}")
FMT = re.compile(r"%[-+ #0-9.]*[a-zA-Z]")
CJK = re.compile(r"[\u4e00-\u9fff]")

# 顺序有意义：先匹配到的桶胜出
BUCKETS = [
    ("log", re.compile(
        r"^(MEM |\*\*\*|\s*$)|WARNING|ERROR|FAILED|unable to|"
        r"relock|Realloc|dealloc|assert|Could not", re.I)),
    ("editor_input", re.compile(
        r"^(Enter|Input|Type|Select this|Click to set|Choose)\b", re.I)),
    ("editor_check", re.compile(
        r"(Unit ID %d|City ID %d|mismatch|should be \d|not an air base|"
        r"pilot number mismatch|in command:|duplicate)", re.I)),
    ("dialog", re.compile(r"~|^\w[\w ]*\?$")),
    ("path", re.compile(
        r"^[\\/]|[\\/]|\.(bmp|jpg|png|wav|txt|csv|dat|scn|sav|dll|exe)\b",
        re.I)),
    ("fmt", re.compile(r"%[-+ #0-9.]*[a-zA-Z]")),
    ("enum", re.compile(r"^[A-Z0-9_]{1,8}( [A-Z0-9_]{1,8})*$")),
    ("ident", re.compile(r"^[a-z][a-z0-9_ ]*$")),
]


def load_rdata(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    base = struct.unpack_from("<I", blob, pe + 24 + 28)[0]
    table = pe + 24 + optsz
    for i in range(nsec):
        o = table + i * 40
        if blob[o:o + 8].rstrip(b"\0") != b".rdata":
            continue
        vs, rva, rs, ra = struct.unpack_from("<IIII", blob, o + 8)
        data = blob[ra:ra + rs]
        out = {}
        i, n = 0, len(data)
        while i < n - 1:
            start, chars = i, []
            while i < n - 1:
                w = data[i] | (data[i + 1] << 8)
                if w == 0:
                    break
                if not (32 <= w < 127):
                    chars = None
                    break
                chars.append(chr(w))
                i += 2
            if chars and len(chars) >= 2 and i < n - 1:
                out.setdefault("".join(chars), []).append(base + rva + start)
                i += 2
            else:
                i = start + 2
        return out
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--bucket")
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit("找不到 %s" % EXE)
    rdata = load_rdata(open(EXE, "rb").read())
    have = set()
    for ln in open(TSV, encoding="utf-8"):
        if ln.startswith("#"):
            continue
        p = ln.rstrip("\n").split("\t")
        if p and p[0]:
            have.add(p[0].lower())

    missing = {t: a for t, a in rdata.items() if t.lower() not in have}
    print(".rdata 去重 %d 条，未收录 %d 条\n" % (len(rdata), len(missing)))

    groups = defaultdict(list)
    for t, addrs in missing.items():
        for name, pat in BUCKETS:
            if pat.search(t):
                groups[name].append(t)
                break
        else:
            groups["other"].append(t)

    order = ["dialog", "editor_input", "editor_check", "fmt",
             "other", "enum", "ident", "path", "log"]
    desc = {
        "dialog": "★ 对话框/多行提示（玩家可见）",
        "editor_input": "编辑器输入提示",
        "editor_check": "编辑器校验输出（日志）",
        "fmt": "格式串（可能是 UI 标签也可能是日志）",
        "other": "其它（未分类，需人工看）",
        "enum": "枚举/全大写代号",
        "ident": "小写标识符（键名）",
        "path": "资源路径/文件名",
        "log": "日志/诊断（玩家看不到）",
    }
    for k in order:
        n = len(groups.get(k, []))
        if n:
            print("  %-30s %5d" % (desc.get(k, k), n))

    if args.bucket:
        items = sorted(groups.get(args.bucket, []), key=lambda s: (-len(s), s))
        print("\n=== %s（%d 条）===" % (args.bucket, len(items)))
        for s in items[:args.show]:
            print("  %r" % s[:88])

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["类别", "说明", "原文", "地址", "字符数", "含格式符"])
            for k in order:
                for t in sorted(groups.get(k, []), key=lambda s: (-len(s), s)):
                    w.writerow([k, desc.get(k, k), t,
                                " ".join(hex(a) for a in missing[t]),
                                len(t),
                                "Y" if FMT.search(t) else ""])
        print("\n逐条 -> %s" % args.csv)


if __name__ == "__main__":
    main()
