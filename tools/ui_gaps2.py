#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
> **已弃用 —— 分母不可信。**
>
> 判据（text == dst 才算没翻）是对的，但分母来自手写 UTF-16 扫描器，
> 且 `wite2_regions.tsv` 可能早于当前部署（时间戳差 2 小时就会得出
> 完全不同的结论）。现在用 `tools/coverage_report.py`。

用运行时捕获精确统计"界面文本还缺什么"

数据源 `wite2_regions.tsv` 是绘制钩子每次调用的记录：

    text = 进入钩子时的原始文本（用户本来会看到的）
    dst  = 钩子替换之后真正画出来的

所以判据很简单：

    text != dst  →  翻了
    text == dst  →  没翻（这是唯一可信的"缺口"定义）

按这个判据分类，不再用 inventory.py 那套"看起来像不像标识符"的猜测。

用法:
    python tools/ui_gaps2.py
    python tools/ui_gaps2.py --csv build/ui_gaps2.csv
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
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")
DICT = os.path.join(ROOT, "zh_dict.json")

CODEPOINT = re.compile(r"<([0-9A-Fa-f]{4,6})>")
CJK = re.compile(r"[\u4e00-\u9fff]")
LETTER = re.compile(r"[A-Za-z]")
WORDISH = re.compile(r"[A-Za-z]{2,}")
SPACE = re.compile(r"\s")


def dec(s):
    """<6E38><620F> → 中文"""
    if "<" in s:
        return "".join(chr(int(m, 16)) for m in CODEPOINT.findall(s))
    return s


def rdata_strings(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    table = pe + 24 + optsz
    for i in range(nsec):
        o = table + i * 40
        nm = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        if nm != ".rdata":
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
                if not (32 <= w < 127):
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
    ap.add_argument("--csv")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    if not os.path.exists(REGIONS):
        sys.exit("找不到 %s" % REGIONS)

    rd = set()
    if os.path.exists(EXE):
        rd = rdata_strings(open(EXE, "rb").read())
    cs = csv_values()

    dic = json.load(open(DICT, encoding="utf-8"))
    keys = {k for k in dic if not k.startswith("_")}
    keyset = {k.lower() for k in keys}

    tally = Counter()
    untranslated = defaultdict(int)
    translated = []
    rows_out = []

    for ln in open(REGIONS, encoding="utf-8", errors="replace"):
        if ln.startswith("#"):
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) < 15:
            continue
        src, dst = dec(p[13]), dec(p[14])
        s, d = src.strip(), dst.strip()
        if not s:
            kind = "empty"
        elif s != d and CJK.search(d):
            kind = "done"                       # 换了，且结果是中文 → 翻了
        elif s == d and CJK.search(s):
            kind = "src_zh"                     # 原文就是中文
        elif s == d and not LETTER.search(s):
            kind = "num"                        # 没换，但没字母 → 数字/符号
        elif s == d and WORDISH.search(s):
            kind = "MISS"                       # 没换，有英文单词 → 真缺口
        elif s == d:
            kind = "short_sym"                  # 没换，只有单字母/符号
        else:
            kind = "other"
        tally[kind] += 1
        if kind == "MISS":
            untranslated[s] += 1
        if kind == "done":
            translated.append((s, d))
        rows_out.append({"kind": kind, "src": s, "dst": d,
                         "x": p[8], "y": p[9], "font": p[12]})

    total = sum(tally.values())
    print("绘制调用 %d 条" % total)
    label = {
        "done": "已翻译（text != dst 且含中文）",
        "MISS": "★ 未翻译（text == dst 且含英文单词）",
        "num": "数字/符号（无需翻）",
        "short_sym": "单字母/符号（缩写，一般不动）",
        "src_zh": "原文即中文",
        "empty": "空",
        "other": "其它",
    }
    for k, _ in tally.most_common():
        print("  %-10s %-36s %5d  %5.1f%%"
              % (k, label.get(k, k), tally[k], tally[k] * 100.0 / total))

    print("\n真实覆盖率：%.1f%%（%d / %d）"
          % (tally["done"] * 100.0 / max(1, tally["done"] + tally["MISS"]),
             tally["done"], tally["done"] + tally["MISS"]))
    print("未翻译去重：%d 条，共 %d 次绘制"
          % (len(untranslated), tally["MISS"]))

    # 对未翻译的再分诊
    buckets = Counter()
    detail = defaultdict(list)
    for s, n in untranslated.items():
        if s in rd and s.lower() not in keyset:
            b = "A 在 .rdata 且词典没有 → 补词条即可"
        elif s.lower() in keyset:
            b = "B 词典有却没命中 → 匹配逻辑 bug"
        elif s in cs:
            b = "C 来自 Dat/CSV → 数据通道，改 .rdata 无效"
        else:
            b = "D 哪儿都没有 → 运行时拼接"
        buckets[b] += n
        detail[b].append((n, s))

    print("\n未翻译分诊：")
    for b in sorted(buckets):
        print("  %-40s %4d 次 / %d 条" % (b, buckets[b], len(detail[b])))

    for b in sorted(detail):
        items = sorted(detail[b], key=lambda x: -x[0])[:args.top]
        print("\n=== %s（前 %d）===" % (b, len(items)))
        for n, s in items:
            print("  ×%-4d %r" % (n, s[:72]))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["kind", "src", "dst",
                                              "x", "y", "font"])
            w.writeheader()
            w.writerows(rows_out)
        print("\n逐条 -> %s" % args.csv)


if __name__ == "__main__":
    main()
