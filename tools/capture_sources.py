#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把绘制层捕获的文本，逐条对到它的来源

`wite2_capture.txt` 是绘制函数 0x85CE60 的运行时捕获，每行是
`栈链<TAB>绘制文本<TAB>改写后文本`。它代表**玩家真正看到的东西**。

本脚本把每条捕获文本分到四类：

    A  rdata   —— exe `.rdata` 里有原文（界面文本，能原地改写）
    B  data    —— `.rdata` 里没有，但能在 Dat/CSV 里找到（数据驱动：部队名/地名）
    C  scn     —— 剧本描述 txt 里的
    D  unknown —— 哪里都找不到（运行时拼出来的）

这个划分直接回答"哪些文本能一对一精准翻译"。

用法:
    python tools/capture_sources.py
    python tools/capture_sources.py --csv build/capture_sources.csv
"""

import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GAME = os.environ.get(
    "WITE2_GAME",
    r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2",
)
CAPTURE = os.path.join(GAME, "wite2_capture.txt")
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")


def rdata_strings(blob):
    """抽 exe .rdata 里所有 UTF-16 串"""
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
        i = 0
        n = len(data)
        while i < n - 1:
            start = i
            chars = []
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


def csv_strings():
    """Dat/CSV 里所有单元格值"""
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


def scn_strings():
    out = set()
    base = os.path.join(GAME, "Dat", "Scen")
    if not os.path.isdir(base):
        return out
    for root, _, names in os.walk(base):
        for n in names:
            if not n.lower().endswith((".txt", ".scn")):
                continue
            try:
                with open(os.path.join(root, n), encoding="utf-8",
                          errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            out.add(line)
            except Exception:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="导出逐条结果")
    args = ap.parse_args()

    if not os.path.exists(CAPTURE):
        sys.exit("找不到 %s" % CAPTURE)
    if not os.path.exists(EXE):
        sys.exit("找不到 %s（先跑 tools/make_decrypted_exe.py）" % EXE)

    rd = rdata_strings(open(EXE, "rb").read())
    cs = csv_strings()
    sc = scn_strings()
    print(".rdata 字符串 %d 条 / CSV 值 %d 个 / 剧本行 %d 条"
          % (len(rd), len(cs), len(sc)))

    rows = []
    for ln in open(CAPTURE, encoding="utf-8", errors="replace"):
        parts = ln.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        chain, raw = parts[0], parts[1]
        if "<" in raw:          # 已经是码点形式（运行时已替换成中文）
            continue
        txt = raw
        if txt in rd:
            src = "A_rdata"
        elif txt in cs:
            src = "B_csv"
        elif txt in sc:
            src = "C_scen"
        else:
            # 前缀/包含匹配：数据拼接出来的
            hit = None
            for v in cs:
                if len(v) >= 4 and (txt.startswith(v) or v in txt):
                    hit = v
                    break
            src = "D_unknown" if hit is None else "B_csv_part"
        rows.append({"src": src, "text": txt, "chain": chain})

    from collections import Counter
    c = Counter(r["src"] for r in rows)
    print("\n绘制捕获 %d 条，来源分布：" % len(rows))
    for k in ("A_rdata", "B_csv", "B_csv_part", "C_scen", "D_unknown"):
        if c.get(k):
            print("  %-12s %4d" % (k, c[k]))

    for k in ("B_csv", "B_csv_part", "D_unknown"):
        sample = [r["text"] for r in rows if r["src"] == k][:12]
        if sample:
            print("\n  %s 样例：" % k)
            for s in sample:
                print("     %r" % s[:64])

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["src", "text", "chain"])
            w.writeheader()
            w.writerows(rows)
        print("\n逐条 -> %s" % args.csv)


if __name__ == "__main__":
    main()
