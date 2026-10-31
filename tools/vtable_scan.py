#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚表扫描：谁把绘制函数放进函数指针表

为什么做这个
------------
`'Game Options'` 在 .rdata 里既没有 push/lea 立即数引用，也没有指针指向它，
但它在游戏里确实显示了。说明"取字符串"这一步不是硬编码的，而是：

    虚表[索引] → 绘制函数 → 从某个对象字段取文本

也就是说，**函数指针表**才是这个引擎的调度中心。扫出所有虚表，
就能知道有多少个绘制入口，而不是只有 README 假设的那一个 0x85CE60。

用法:
    python tools/vtable_scan.py                    # 列表
    python tools/vtable_scan.py --find 0x85ce60    # 这个函数在哪些虚表里
    python tools/vtable_scan.py --csv out.csv
"""

import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")


def parse_pe(blob):
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


def vtables(blob, base, segs, min_run=6):
    """在 .rdata/.data 里找连续 >=min_run 个 dword 都指向 .text 的块"""
    t = segs[".text"]
    t_lo = base + t["rva"]
    t_hi = t_lo + t["rs"]
    out = []
    for name in (".rdata", ".data"):
        s = segs.get(name)
        if not s:
            continue
        data = blob[s["fp"]:s["fp"] + s["rs"]]
        n = len(data)
        i = 0
        while i <= n - 4:
            run = []
            j = i
            while j <= n - 4:
                v = struct.unpack_from("<I", data, j)[0]
                if not (t_lo <= v < t_hi):
                    break
                run.append((base + s["rva"] + j, v))
                j += 4
            if len(run) >= min_run:
                out.append({"seg": name, "start": run[0][0],
                            "count": len(run), "entries": run})
                i = j
            else:
                i += 4
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find", nargs="*", type=lambda s: int(s, 16))
    ap.add_argument("--csv")
    ap.add_argument("--min", type=int, default=6)
    args = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit("找不到 %s" % EXE)
    blob = open(EXE, "rb").read()
    base, segs = parse_pe(blob)
    vts = vtables(blob, base, segs, args.min)

    total = sum(v["count"] for v in vts)
    print("找到 %d 个虚表块，函数指针合计 %d 个" % (len(vts), total))
    print("（判定：连续 ≥%d 个 dword 落在 .text 里）\n" % args.min)

    big = sorted(vts, key=lambda v: -v["count"])[:15]
    print("最大的 15 个：")
    for v in big:
        print("  %-8s %#09x  %3d 项  首个=%#x 末项=%#x"
              % (v["seg"], v["start"], v["count"],
                 v["entries"][0][1], v["entries"][-1][1]))

    if args.find:
        print()
        for target in args.find:
            hits = [(v, k) for v in vts
                    for k, (_, f) in enumerate(v["entries"]) if f == target]
            print("  %#09x 在 %d 个虚表里" % (target, len(hits)))
            for v, idx in hits:
                print("      %s %#09x 索引 %d" % (v["seg"], v["start"], idx))

    if args.csv:
        rows = []
        for v in vts:
            for idx, (slot, fn) in enumerate(v["entries"]):
                rows.append({"vtable": hex(v["start"]), "seg": v["seg"],
                             "index": idx, "slot": hex(slot),
                             "function": hex(fn)})
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["vtable", "seg", "index",
                                              "slot", "function"])
            w.writeheader()
            w.writerows(rows)
        print("\n全部虚表项 %d 条 -> %s" % (len(rows), args.csv))


if __name__ == "__main__":
    main()
