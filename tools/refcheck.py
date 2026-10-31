#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引用风险检查 —— 用静态引用证据给每条译文定风险等级

背景
----
`.rdata` 里混着三样东西：纯显示文本、数据表键名、存档字段名。
以前只能靠正则猜（"看起来像标识符"就危险），漏判和误判都不少。

这里换一个更强的判据：**看这条字符串在二进制里有没有被引用**。

    .text 里出现它的地址（立即数）      → 代码直接引用它
    .data 里有 dword 指向它             → 全局表持有它（键名/枚举/存档字段）
    .rdata 里有 dword 指向它            → 只读表持有它
    哪儿都没有                          → 只可能是 UI 层显示文本

证据来源是磁盘上的 exe（.text 需要已解密的版本），不需要 Ghidra，
几秒跑完，可以每次改词典都跑。

用法
----
    python tools/refcheck.py                    # 汇总 + 高危清单
    python tools/refcheck.py --csv out.csv      # 导出全部条目
    python tools/refcheck.py --risky            # 只打印高危条目
    python tools/refcheck.py --exe <path>       # 指定 exe（默认找解密版）

解密版 exe 的来历见 tools/make_decrypted_exe.py。
"""

import argparse
import csv
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"

# PE 段名 -> 扫描用途
WANTED = (".text", ".rdata", ".data")


def parse_pe(blob):
    """返回 (image_base, {段名: {rva, file_offset, raw_size, virt_size}})

    段表里存的是 RVA，运行时地址 = image_base + rva（这个 exe 是 0x400000）。
    游戏内部、README 和 hook_text.c 里的地址都是 RVA，报给用户时统一换成 VA。
    """
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    image_base = struct.unpack_from("<I", blob, pe + 24 + 28)[0]
    table = pe + 24 + optsz
    segs = {}
    for i in range(nsec):
        o = table + i * 40
        name = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        vs, va, rs, ra = struct.unpack_from("<IIII", blob, o + 8)
        segs[name] = {"rva": va, "file_offset": ra, "raw_size": rs, "virt_size": vs}
    return image_base, segs


def scan_points_into(blob, src, dst_lo, dst_hi):
    """扫描 src 段里所有 4 字节（不要求对齐）落在 [dst_lo,dst_hi) 的 dword。

    返回 {目标地址: [源地址, ...]}
    """
    import numpy as np

    off = src["file_offset"]
    n = src["raw_size"]
    if n < 4:
        return {}
    a = np.frombuffer(blob, dtype=np.uint8, count=n, offset=off)
    w = np.lib.stride_tricks.sliding_window_view(a, 4).astype(np.uint32)
    vals = w[:, 0] | (w[:, 1] << 8) | (w[:, 2] << 16) | (w[:, 3] << 24)
    idx = np.nonzero((vals >= dst_lo) & (vals < dst_hi))[0]
    out = {}
    base = src["rva"]
    for i in idx.tolist():
        v = int(vals[i])
        out.setdefault(v, []).append(base + i)
    return out


# 这些 opcode 后面的 4 字节才是"真的当地址用"。
# 排除 E8/E9（call/jmp rel32）很关键：5 字节 call 的后 4 字节经常恰好
# 拼出一个落在 .rdata 里的数，不排除就会报一堆假阳性。
IMM32_OPCODES = {
    0x68,                                    # push imm32
    0xA1, 0xA3,                              # mov eax,[imm32] / mov [imm32],eax
    0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D,   # ALU eax, imm32
    0xC7,                                    # mov r/m32, imm32（含 C7 05 xx xx xx xx）
} | set(range(0xB8, 0xC0))                  # mov r32, imm32


def is_code_reference(blob, ref_rva, segs):
    """ref_rva 处的 4 字节是否真的被当地址用（而不是指令中间的巧合字节）

    只看紧邻的前一个字节是不是上面那张表里的 opcode。粗糙但够用：
    真引用一定是 `68 imm32` / `B8+ imm32` / `C7 05 imm32` 这类形式。
    """
    t = segs[".text"]
    o = t["file_offset"] + (ref_rva - t["rva"])
    if o <= 0 or o >= len(blob):
        return False
    prev = blob[o - 1]
    if prev == 0xC7:      # 需要再往前看 modrm 必须是 05（绝对地址）
        return o >= 2 and blob[o - 2] == 0x05
    return prev in IMM32_OPCODES


def utf16_strings(blob, seg, min_len=2):
    """列出段内所有 UTF-16LE 字符串：(va, text)"""
    off = seg["file_offset"]
    n = seg["raw_size"]
    data = blob[off:off + n]
    res = []
    i = 0
    while i < n - 1:
        # 找下一个可打印宽字符起点（要求偶对齐）
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
        if chars and len(chars) >= min_len and i < n - 1:
            res.append((seg["rva"] + start, "".join(chars)))
            i += 2
        else:
            i = start + 2
    return res


def load_captured(path):
    """绘制层捕获里出现过的英文文本（wite2_capture.txt 第二列）"""
    got = set()
    if not os.path.exists(path):
        return got
    for ln in open(path, encoding="utf-8", errors="replace"):
        parts = ln.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        raw = parts[1]
        if "<" in raw:            # 已转成码点的中文
            continue
        got.add(raw.strip())
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--dict", default=os.path.join(ROOT, "zh_dict.json"))
    ap.add_argument("--csv", help="把全部条目写到这个 CSV")
    ap.add_argument("--risky", action="store_true", help="只打印高危条目")
    ap.add_argument("--limit", type=int, default=40, help="打印条数上限")
    args = ap.parse_args()

    if not os.path.exists(args.exe):
        sys.exit(
            "找不到 %s\n先跑 python tools/make_decrypted_exe.py 生成解密版 exe。"
            % args.exe
        )

    blob = open(args.exe, "rb").read()
    image_base, segs = parse_pe(blob)
    missing = [s for s in WANTED if s not in segs]
    if missing:
        sys.exit("exe 缺少段: %s" % missing)

    text, rdata, data = segs[".text"], segs[".rdata"], segs[".data"]
    rd_lo, rd_hi = rdata["rva"], rdata["rva"] + rdata["raw_size"]

    # 谁引用了 .rdata 里的地址
    ref_from_text = scan_points_into(blob, text, rd_lo, rd_hi)
    ref_from_rdata = scan_points_into(blob, rdata, rd_lo, rd_hi)
    ref_from_data = scan_points_into(blob, data, rd_lo, rd_hi)

    # .rdata 里所有 UTF-16 串
    strings = utf16_strings(blob, rdata)
    by_text = {}
    for rva, s in strings:
        by_text.setdefault(s, []).append(rva)

    captured = load_captured(os.path.join(GAME, "wite2_capture.txt"))

    dic = json.load(open(args.dict, encoding="utf-8"))
    meta = {k for k in dic if k.startswith("_")}

    rows = []
    for en, zh in dic.items():
        if en in meta or not isinstance(zh, str):
            continue
        addrs = by_text.get(en, [])
        if not addrs:
            risk, why = "absent", "rdata 里没有这条串（可能只在 .data 段或数据文件里）"
        else:
            code_sites = [r for a in addrs for r in ref_from_text.get(a, [])]
            code_sites = [r for r in code_sites
                          if is_code_reference(blob, r, segs)]
            code = len(code_sites)
            dref = sum(1 for a in addrs if a in ref_from_data)
            rref = sum(1 for a in addrs if a in ref_from_rdata)
            if code:
                risk, why = "critical", "%d 处 .text 立即数引用 —— 代码直接用它" % code
            elif dref:
                risk, why = "high", "%d 个项目符号指向它（.data 全局表）" % dref
            elif rref:
                risk, why = "medium", "%d 个只读指针指向它（.rdata 表）" % rref
            elif en in captured:
                risk, why = "low", "只被绘制层见到过，无任何指针引用"
            else:
                risk, why = "low", "无任何引用"
        rows.append(
            {
                "risk": risk,
                "key": en,
                "zh": zh,
                "addrs": " ".join(hex(image_base + a) for a in addrs),
                "why": why,
            }
        )

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "absent": 4}
    rows.sort(key=lambda r: (order[r["risk"]], r["key"]))

    counts = {}
    for r in rows:
        counts[r["risk"]] = counts.get(r["risk"], 0) + 1

    print("exe: %s" % args.exe)
    print("词条: %d（不含元数据）" % len(rows))
    for k in ("critical", "high", "medium", "low", "absent"):
        if k in counts:
            print("  %-8s %5d" % (k, counts[k]))
    print()

    show = [r for r in rows if r["risk"] in ("critical", "high")] if args.risky \
        else rows
    n = 0
    for r in show:
        if r["risk"] in ("critical", "high", "medium"):
            print("[%s] %-42s %s" % (r["risk"][:4].upper(), r["key"][:42], r["why"]))
            n += 1
            if n >= args.limit:
                print("  ...（--limit 控制条数）")
                break

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["risk", "key", "zh", "addrs", "why"])
            w.writeheader()
            w.writerows(rows)
        print("\n写出 %d 条 -> %s" % (len(rows), args.csv))


if __name__ == "__main__":
    main()
