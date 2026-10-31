#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建权威的 .rdata 字符串清单（Ghidra ∪ 修好的自扫描）

为什么要两份
------------
Ghidra 只列**已定义**的 string Data，未识别的原始字节它不看 —— 所以
`'Export to CSV?'`（0x98930C）在 Ghidra 的 20174 条里没有。

自己扫字节能补上，但 .rdata 里 ASCII 串和 UTF-16 串**混排**：ASCII 的
单字节 `\\0` 会改变 UTF-16 的对齐，于是扫出 `'sCreate Depot Here ?'`
这种把前一条 ASCII 串尾巴（"South Pari**s**"）粘进来的假串。

修法：自扫描只接受**前两个字节恰好是 `00 00`** 的起点。这会漏掉
紧跟 ASCII 串的 UTF-16 串（少见，且这类基本已被 Ghidra 覆盖），
但杜绝了假串。

产物 build/rea/strings_authoritative.json: [{addr, text, src}]

用法:
    python tools/string_inventory.py
    python tools/string_inventory.py --diff    # 与旧扫描对比，看修掉多少假串
"""

import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")
GHIDRA = os.path.join(ROOT, "build", "rea", "allstrings.json")
OUT = os.path.join(ROOT, "build", "rea", "strings_authoritative.json")


def pe_info(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    base = struct.unpack_from("<I", blob, pe + 24 + 28)[0]
    table = pe + 24 + optsz
    segs = {}
    for i in range(nsec):
        o = table + i * 40
        nm = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        vs, rva, rs, ra = struct.unpack_from("<IIII", blob, o + 8)
        segs[nm] = {"rva": rva, "fp": ra, "rs": rs}
    return base, segs


def scan_strict(blob, base, seg):
    """严格扫描：只接受前两字节为 00 00 的 UTF-16 起点"""
    data = blob[seg["fp"]:seg["fp"] + seg["rs"]]
    va0 = base + seg["rva"]
    out = []
    n = len(data)
    i = 0
    while i < n - 3:
        # 起点条件：i>=2 时前两字节必须是 00 00；i==0 时无条件
        if i >= 2 and data[i - 2:i] != b"\x00\x00":
            i += 2
            continue
        if data[i + 1] != 0:          # 必须是小端 ASCII 宽字符
            i += 2
            continue
        lo = data[i]
        if not (32 <= lo < 127):
            i += 2
            continue
        start = i
        chars = []
        while i < n - 1 and data[i + 1] == 0 and 32 <= data[i] < 127:
            chars.append(chr(data[i]))
            i += 2
        if len(chars) >= 2 and data[i:i + 2] == b"\x00\x00":
            out.append((va0 + start, "".join(chars)))
        elif len(chars) < 2:
            i = start + 2
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit("找不到 %s" % EXE)
    blob = open(EXE, "rb").read()
    base, segs = pe_info(blob)

    strict = scan_strict(blob, base, segs[".rdata"])
    print("自扫描（严格对齐）: %d 条" % len(strict))

    ghidra = []
    if os.path.exists(GHIDRA):
        d = json.load(open(GHIDRA, encoding="utf-8"))
        n = d.get("normalized_result") or d.get("raw_result") or []
        for s in n:
            va = int(s["address"], 16)
            if base + segs[".rdata"]["rva"] <= va < base + segs[".rdata"]["rva"] + segs[".rdata"]["rs"]:
                ghidra.append((va, s["value"]))
        print("Ghidra 已定义串（.rdata）: %d 条" % len(ghidra))
    else:
        print("（没有 build/rea/allstrings.json，只用自扫描）")

    merged = {}
    for va, t in ghidra:
        merged.setdefault(t, {"addr": va, "src": "ghidra"})
    added = 0
    for va, t in strict:
        if t not in merged:
            merged[t] = {"addr": va, "src": "scan"}
            added += 1
        elif merged[t]["src"] == "ghidra":
            pass
    print("合并去重后 %d 条（自扫描补充 %d 条 Ghidra 没有的）"
          % (len(merged), added))

    if args.diff:
        old = os.path.join(ROOT, "build", "rea", "gap_triage.csv")
        if os.path.exists(old):
            import csv
            oldtext = set()
            for r in csv.DictReader(open(old, encoding="utf-8-sig")):
                oldtext.add(r["原文"])
            bogus = [t for t in oldtext if t not in merged]
            print("\n旧清单里有 %d 条在新权威集里不存在（假串或未定义）："
                  % len(bogus))
            for t in sorted(bogus, key=len)[:25]:
                print("   %r" % t[:80])

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump([{"addr": hex(v["addr"]), "text": k, "src": v["src"]}
                   for k, v in sorted(merged.items(), key=lambda kv: kv[1]["addr"])],
                  f, ensure_ascii=False, indent=1)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
