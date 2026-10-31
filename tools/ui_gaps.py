#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
> **已弃用 —— 逻辑有两处错，不要用。**
>
> 1. 把 `wite2_regions.tsv` 的第 14 列（text，钩子进入时的原文）当成
>    "实际画出来的"，于是把原文本来就是中文的 897 条算成了"未翻译"。
> 2. 分母来自手写扫描器，含假串。
>
> 现在用 `tools/coverage_report.py`（权威字符串清单 + `~`/占位符校验）。

界面覆盖缺口分析 —— 玩家实际看到的英文，到底哪些是"该翻而没翻"

数据源是运行时绘制捕获 `wite2_regions.tsv`（每次 DrawText 一行，带坐标和
ui_idx），它代表**玩家真正看到的东西**，不是我们猜的"可能显示"。

每条绘制文本分到六类：

    zh      已经是中文（翻到了）
    num     纯数字/符号（日期、坐标、0-50 这种，不需要翻）
    MISS    在 .rdata 里、且能翻、但词典没有   ← ★ 真正的缺口
    DATA    在 Dat/CSV 里（部队名/地名/装备名）→ 走数据通道，不是 .rdata 的活
    SCN     在剧本描述里
    RT      哪都没有 → 运行时拼出来的

用法:
    python tools/ui_gaps.py
    python tools/ui_gaps.py --csv build/ui_gaps.csv
    python tools/ui_gaps.py --top 60
"""

import argparse
import csv
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GAME = os.environ.get(
    "WITE2_GAME",
    r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2",
)
REGIONS = os.path.join(GAME, "wite2_regions.tsv")
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decoded.exe")
DECRYPTED = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")
DICT = os.path.join(ROOT, "zh_dict.json")

CODEPOINT = re.compile(r"<([0-9A-Fa-f]{4,6})>")
CJK = re.compile(r"[\u4e00-\u9fff]")
ALPHA = re.compile(r"[A-Za-z]")

# 不值得翻的：纯数字/日期/坐标/编号
NUMERIC = re.compile(
    r"^[\s\d\-\.:,/()%+*#xX×°]*$"
)
# 部队番号/缩写（本项目约定不翻）
UNIT_ABBR = re.compile(
    r"^(I{1,3}|IV|V|VI{0,3}|IX|X{1,3})?(/)?\d*\s*"
    r"(Pz|ID|Inf|Mot|Cav|Mtn|Sec|LW|SS|GD|PzG|StG|JG|KG|SG|TG|ZG|NJG|SKG|"
    r"FJG|IAD|IAK|SAD|BAD|BAK|GBAD|GBAK|GIAD|ChF|KBF|LW|HQ|AP|T|of)\b",
    re.I,
)


def decode_text(raw):
    """`<6E38><620F>` → 中文；原样英文直接返回"""
    if "<" in raw:
        return "".join(chr(int(m, 16)) for m in CODEPOINT.findall(raw))
    return raw


def rdata_strings(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    table = pe + 24 + optsz
    for i in range(nsec):
        o = table + i * 40
        name = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        if name != ".rdata":
            continue
        vs, rva, rs, ra = struct.unpack_from("<IIII", blob, o + 8)
        data = blob[ra:ra + rs]
        out = set()
        i, n = 0, len(data)
        while i < n - 1:
            start, chars = i, []
            while i < n - 1:
                w = data[i] | (data[i + 1] << 8)
                if w == 0:
                    break
                if not (32 <= w < 127 or 0xA0 <= w <= 0x24F):
                    chars = None
                    break
                chars.append(chr(w))
                i += 2
            if chars and len(chars) >= 2 and i < n - 1:
                out.add("".join(chars))
                i += 2
            else:
                i = start + 2
        return out
    return set()


def csv_values():
    out = set()
    base = os.path.join(GAME, "Dat", "CSV")
    for root, _, names in os.walk(base):
        for n in names:
            if not n.lower().endswith(".csv"):
                continue
            try:
                with open(os.path.join(root, n), encoding="utf-8-sig",
                          errors="surrogateescape", newline="") as f:
                    for row in csv.reader(f):
                        for v in row:
                            v = v.strip()
                            if v:
                                out.add(v)
            except Exception:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="导出逐条结果")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    exe = EXE if os.path.exists(EXE) else DECRYPTED
    if not os.path.exists(exe):
        sys.exit("找不到 exe（先跑 tools/make_decrypted_exe.py）")
    if not os.path.exists(REGIONS):
        sys.exit("找不到 %s" % REGIONS)

    print("exe: %s" % os.path.basename(exe), file=sys.stderr)
    rd = rdata_strings(open(exe, "rb").read())
    cs = csv_values()
    dic = json.load(open(DICT, encoding="utf-8"))
    keys = {k for k in dic if not k.startswith("_")}
    keyset = {k.lower() for k in keys}

    rows = []
    for ln in open(REGIONS, encoding="utf-8", errors="replace"):
        if ln.startswith("#"):
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) < 15:
            continue
        rows.append({"ui": p[3], "x": p[8], "y": p[9], "raw": p[13]})

    tally = Counter()
    miss = defaultdict(list)     # 该翻没翻
    data_hit = Counter()
    records = []

    for r in rows:
        t = decode_text(r["raw"])
        s = t.strip()
        if not s:
            kind = "empty"
        elif CJK.search(s):
            kind = "zh"
        elif NUMERIC.match(s):
            kind = "num"
        elif s in rd and s.lower() not in keyset:
            kind = "MISS"
        elif s in keyset:
            kind = "dict_nomatch"     # 词典有，但这次没命中 → 匹配逻辑 bug
        elif s in cs:
            kind = "DATA"
        elif UNIT_ABBR.match(s):
            kind = "unit_abbr"
        else:
            kind = "RT"
        tally[kind] += 1
        if kind == "MISS":
            miss[s].append(r)
        if kind == "DATA":
            data_hit[s] += 1
        records.append({"kind": kind, "text": s, "ui": r["ui"],
                        "x": r["x"], "y": r["y"]})

    total = sum(tally.values())
    print("\n绘制调用 %d 条，分类：" % total)
    label = {
        "zh": "已是中文",
        "num": "纯数字/符号（不需要翻）",
        "MISS": "★ 在 .rdata 里但词典没有（真缺口）",
        "dict_nomatch": "词典有却没命中（匹配 bug）",
        "DATA": "来自 Dat/CSV（数据通道）",
        "unit_abbr": "部队番号/缩写（约定不翻）",
        "RT": "运行时拼接（无处可改）",
        "empty": "空",
    }
    for k, _ in tally.most_common():
        print("  %-14s %-34s %5d  %5.1f%%"
              % (k, label.get(k, k), tally[k], tally[k] * 100.0 / total))

    real = tally["MISS"] + tally["dict_nomatch"]
    print("\n>>> 真正需要补的界面文本：%d 条绘制调用，去重 %d 条"
          % (real, len(miss)))

    if miss:
        print("\n=== 该翻没翻的，按出现次数排序（前 %d）===" % args.top)
        for s, rs in sorted(miss.items(), key=lambda kv: -len(kv[1]))[:args.top]:
            print("  ×%-4d %r" % (len(rs), s[:70]))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["kind", "text", "ui", "x", "y"])
            w.writeheader()
            w.writerows(records)
        print("\n逐条 -> %s" % args.csv)


if __name__ == "__main__":
    main()
