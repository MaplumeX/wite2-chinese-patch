#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重扫：x86 里引用一个绝对地址，不止 push imm32

之前的扫描只认 `68`(push imm32) / `B8+`(mov reg,imm32) / `A1` / `C7 05`，
于是得出"菜单文本零静态引用"的结论。这个结论很可能是错的，因为：

    LEA EAX, [EBX + 0x9B7970]   →   8D 83 70 79 9B 00
                                        ^^^^^ ^^^^^^^^^^^
                                        opcode  disp32  ← 我根本没查这个

MOV 也一样：
    MOV EAX, [0x9B7970]         →   8B 05 70 79 9B 00
    MOV [0x9B7970], EAX         →   89 05 70 79 9B 00
    CALL [0x9B7970]             →   FF 15 70 79 9B 00

本脚本枚举所有"后跟 4 字节绝对地址"的编码形式，重扫 .text。

用法:
    python tools/xref_scan.py               # 汇总
    python tools/xref_scan.py --check 0x9b7970 0x9bb3f0
    python tools/xref_scan.py --csv out.csv
"""

import argparse
import csv
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")

# (opcode 字节, 掩码, 说明) —— 掩码用来处理 B8..BF 这种带寄存器位的
# 判定方式：最后 4 字节是 disp32/imm32 绝对地址，前面必须精确匹配 (opcode & mask)
FORMS = [
    (0x68, 0xFF, "push imm32"),
    (0x8D, 0x00, "lea r32, [disp32] / [reg+disp32]"),   # 特殊处理
    (0x8B, 0x00, "mov r32, [disp32]"),                   # 特殊处理
    (0x89, 0x00, "mov [disp32], r32"),                   # 特殊处理
    (0xC7, 0xFF, "mov [disp32], imm32 (C7 05)"),
    (0xFF, 0x00, "call/jmp/inc [disp32]"),               # 特殊处理
    (0xA1, 0xFF, "mov eax, [disp32]"),
    (0xA3, 0xFF, "mov [disp32], eax"),
]
ALU_IMM = [(0x05, "add eax"), (0x0D, "or eax"), (0x15, "adc eax"),
           (0x1D, "sbb eax"), (0x25, "and eax"), (0x2D, "sub eax"),
           (0x35, "xor eax"), (0x3D, "cmp eax")]
# 这些 ModRM 的 r/m=101 + mod=00 → 绝对 disp32（无基址寄存器）
RM_ABS = {0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D}
# mod=10 (disp32) 且 r/m 合法 → 有基址寄存器 + disp32
RM_DISP32 = {0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
             0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E, 0x8F}


def scan(blob):
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
    t = segs[".text"]
    text = blob[t["fp"]:t["fp"] + t["rs"]]
    va0 = base + t["rva"]

    hits = defaultdict(list)      # 目标 VA -> [(引用 VA, 形式)]
    for i in range(len(text) - 6):
        op = text[i]
        nxt = text[i + 1]
        form = None

        if op == 0x68:
            form = "push imm32"
        elif op in (0xA1, 0xA3):
            form = "mov eax,[disp32]" if op == 0xA1 else "mov [disp32],eax"
        elif op == 0xC7 and nxt == 0x05:
            form = "mov [disp32],imm32"
        elif 0xB8 <= op <= 0xBF:
            form = "mov r32,imm32"
        elif op in (0x8D, 0x8B, 0x89, 0xFF):
            m = nxt
            if m in RM_ABS:
                form = {0x8D: "lea r32,[disp32]", 0x8B: "mov r32,[disp32]",
                        0x89: "mov [disp32],r32", 0xFF: "call/jmp/inc [disp32]"}[op]
            elif m in RM_DISP32:
                # [reg + disp32]，可能是 LEA 取地址，也可能是访问结构体字段
                form = {0x8D: "lea r32,[reg+disp32]",
                        0x8B: "mov r32,[reg+disp32]",
                        0x89: "mov [reg+disp32],r32",
                        0xFF: "call/jmp [reg+disp32]"}[op]
        else:
            for aluop, name in ALU_IMM:
                if op == aluop:
                    form = name + ",imm32"
                    break

        if not form:
            continue
        imm_off = i + 2 if (op in (0xC7, 0x8D, 0x8B, 0x89, 0xFF)) else i + 1
        if imm_off + 4 > len(text):
            continue
        v = struct.unpack_from("<I", text, imm_off)[0]
        if base <= v < base + 0x7000000:
            hits[v].append((va0 + i, form))
    return base, segs, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", nargs="*", type=lambda s: int(s, 16),
                    help="检查这些地址（十六进制）")
    ap.add_argument("--csv")
    args = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit("找不到 %s（先跑 tools/make_decrypted_exe.py）" % EXE)
    blob = open(EXE, "rb").read()
    base, segs, hits = scan(blob)

    rd = segs[".rdata"]
    rd_lo = base + rd["rva"]
    rd_hi = rd_lo + rd["rs"]

    by_form = Counter()
    in_rdata = 0
    for v, sites in hits.items():
        for _, f in sites:
            by_form[f] += 1
        if rd_lo <= v < rd_hi:
            in_rdata += 1

    print("绝对地址引用总数 %d 处，指向 .rdata 的目标 %d 个"
          % (sum(len(s) for s in hits.values()), in_rdata))
    print("\n按编码形式：")
    for f, n in by_form.most_common():
        print("  %-30s %5d" % (f, n))

    if args.check:
        print("\n=== 指定地址检查 ===")
        for v in args.check:
            sites = hits.get(v, [])
            print("  %#09x  引用 %d 处" % (v, len(sites)))
            for a, f in sites[:12]:
                print("      %#09x  %s" % (a, f))

    if args.csv:
        rows = []
        # 建 字符串地址 -> 文本 映射，方便导出可读结果
        data = blob[rd["fp"]:rd["fp"] + rd["rs"]]

        def s_at(va):
            o = va - rd_lo
            if o < 0 or o + 2 > len(data):
                return None
            s = b""
            while o + 1 < len(data):
                w = data[o] | (data[o + 1] << 8)
                if w == 0:
                    break
                if not (32 <= w < 127):
                    return None
                s += bytes([w])
                o += 2
                if len(s) > 120:
                    return None
            return s.decode("ascii") if len(s) >= 2 else None

        for v, sites in hits.items():
            txt = s_at(v) if rd_lo <= v < rd_hi else None
            if not txt:
                continue
            for a, f in sites:
                rows.append({"target": hex(v), "text": txt,
                             "ref": hex(a), "form": f})
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["target", "text", "ref", "form"])
            w.writeheader()
            w.writerows(rows)
        print("\n能解成字符串的引用 %d 条 -> %s" % (len(rows), args.csv))


if __name__ == "__main__":
    main()
