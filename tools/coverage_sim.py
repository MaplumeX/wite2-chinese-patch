#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
> **已弃用 —— 用 `tools/coverage_report.py` 代替。**
>
> 本脚本的分母来自手写 UTF-16 扫描器，它在 .rdata 的 ASCII/UTF-16 混排区
> 会错位（把 "South Pari**s**" 的尾巴粘到 "Create Depot Here ?" 前面），
> 算出的 10128 条 / 52.2% 不可信。保留仅供对照。

模拟当前部署的真实效果 —— 不依赖任何历史捕获

为什么需要这个
--------------
`wite2_regions.tsv` 是某次运行抓的（时间戳可能早于当前部署的翻译表），
拿它统计"还缺多少"会得到过期数字。这里改为**从当前文件静态推演**：

    输入：exe .rdata 的全部 UTF-16 串 + wite2_zh.tsv（正在用的翻译表）
    规则：复刻 hook_text.c 的两条机制

机制一 —— 启动时原地改写 .rdata（patch_section）
    只有 scope 为空或 'r' 的词条参与；要求译文 UTF-16 字节数
    不超过原文（含结尾 NUL），否则跳过。

机制二 —— 绘制层换指针（0x85CE60 的 hook）
    任意 scope 的词条都参与，整串精确匹配（大小写不敏感），
    不受长度限制（换指针，不原地写）。

两条都覆盖不到的串，就是"玩家会看到英文"的地方。

用法:
    python tools/coverage_sim.py
    python tools/coverage_sim.py --csv build/coverage_sim.csv
    python tools/coverage_sim.py --show-gap 40
"""

import argparse
import csv
import json
import os
import re
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

LETTER = re.compile(r"[A-Za-z]")
WORD = re.compile(r"[A-Za-z]{2,}")
CJK = re.compile(r"[\u4e00-\u9fff]")

# 明显不该翻的（资源路径、扩展名、格式说明符、纯标识符）
SKIP_PAT = [
    (re.compile(r"^[\\/].*|.*[\\/].*\.(bmp|jpg|png|wav|txt|csv|dat|scn|sav|dll|exe|ttf)$", re.I), "资源路径"),
    (re.compile(r"^%[-+ #0-9.]*[a-zA-Z]+$"), "格式说明符"),
    (re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$"), "单标识符"),
    (re.compile(r"^[A-Z0-9_ ]{1,5}$"), "全大写缩写"),
    (re.compile(r"^[\W\d_]+$"), "无字母"),
]


def load_rdata(blob):
    """返回 [(rva, text)]"""
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
        out = []
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
                out.append((base + rva + start, "".join(chars)))
                i += 2
            else:
                i = start + 2
        return out
    return []


def load_tsv(path):
    """{key: (val, scope)}  加上小写索引"""
    exact, lower = {}, {}
    for ln in open(path, encoding="utf-8"):
        if ln.startswith("#"):
            continue
        p = ln.rstrip("\n").split("\t")
        if not p or not p[0]:
            continue
        val = p[1] if len(p) > 1 else ""
        if not val:
            continue
        scope = p[2] if len(p) > 2 else ""
        exact[p[0]] = (val, scope)
        lower.setdefault(p[0].lower(), (p[0], val, scope))
    return exact, lower


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--show-gap", type=int, default=0)
    args = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit("找不到 %s（先跑 tools/make_decrypted_exe.py）" % EXE)
    if not os.path.exists(TSV):
        sys.exit("找不到 %s（先跑 build.bat assets）" % TSV)

    rdata = load_rdata(open(EXE, "rb").read())
    exact, lower = load_tsv(TSV)
    print(".rdata 字符串 %d 条（含重复）  /  翻译表 %d 条" % (len(rdata), len(exact)))

    # 按文本去重，保留所有地址
    by_text = {}
    for rva, t in rdata:
        by_text.setdefault(t, []).append(rva)

    tally = Counter()
    rows = []
    gaps = []

    for t, addrs in by_text.items():
        key = t if t in exact else None
        src, val, scope = (t, exact[t][0], exact[t][1]) if key else (
            lower.get(t.lower(), (None, None, None)))
        if src is None:
            tally["未收录"] += 1
            gaps.append(("未收录", t, addrs))
            rows.append({"status": "未收录", "text": t,
                         "addrs": " ".join(hex(a) for a in addrs), "zh": ""})
            continue

        # 机制一：启动时原地改写
        if scope in ("t", "d"):
            inplace = "跳过（scope=%s）" % scope
        elif len(val.encode("utf-16-le")) > len(t.encode("utf-16-le")):
            inplace = "跳过（译文更长 %d>%d 字节）" % (
                len(val.encode("utf-16-le")), len(t.encode("utf-16-le")))
        else:
            inplace = "改写"
        # 机制二：绘制层
        draw = "可替换"

        if inplace == "改写" or draw == "可替换":
            tally["覆盖"] += 1
            status = "覆盖"
            if inplace != "改写":
                status = "仅绘制层"
                tally["仅靠绘制层"] += 1
        else:
            tally["漏掉"] += 1
            status = "漏掉"
            gaps.append(("漏掉", t, addrs))
        rows.append({"status": status, "text": t,
                     "addrs": " ".join(hex(a) for a in addrs), "zh": val})

    total = len(by_text)
    print("\n按去重文本计 %d 条：" % total)
    print("  覆盖            %5d  (%.1f%%)" % (tally["覆盖"],
                                              tally["覆盖"] * 100.0 / total))
    print("    其中仅靠绘制层   %5d" % tally["仅靠绘制层"])
    print("  未收录（词典没有） %5d  (%.1f%%)" % (tally["未收录"],
                                                 tally["未收录"] * 100.0 / total))
    print("  收录但漏掉       %5d" % tally["漏掉"])

    # 未收录里挑"看起来该翻"的
    def looks_ui(s):
        for pat, why in SKIP_PAT:
            if pat.match(s):
                return None
        if not WORD.search(s):
            return None
        return "UI候选"

    cand = [t for _, t, _ in gaps if looks_ui(t)]
    print("\n未收录 %d 条里，按严判据筛出 UI 候选 %d 条"
          % (tally["未收录"], len(cand)))

    if args.show_gap:
        cand.sort(key=lambda s: (-len(s.split()), -len(s)))
        print()
        for s in cand[:args.show_gap]:
            print("  %r" % s[:78])

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["status", "text", "addrs", "zh"])
            w.writeheader()
            w.writerows(rows)
        print("\n逐条 -> %s" % args.csv)


if __name__ == "__main__":
    main()
