#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存档段名表检查 —— 这些串绝对不能翻

发现过程
--------
`.rdata` 里 0x9A41B0 起有一张 65 项的指针表（66 项里 65 项带 reloc 修正，
是真正的指针表），指向：

    save version / map hex data / sevices / aircraft / ground elements /
    toe(ob) / leaders / ground types / nations / locations / units /
    air groups / planes / pilots / flights / pools (ground) / ...
    hex control / battles data / factory data / game events / loadmap / withdraw

这是**存档文件的数据块名**。玩家的 `.sav` 和 `.dat` 一样是加密的
（前 11 字节 `10 95 ff f9 e0 33 b4 f1 9e 79 ed` 完全相同），所以无法
直接验证它们是否落进文件。

为什么不翻
----------
1. **零收益**：这些是存档内部标签，玩家界面上永远看不到。
2. **风险明确**：如果它们参与存档读写，改名后新档自洽，但**所有老档全部失效**；
   多人对战交换存档也会因为段名不一致而出问题。
3. 静态上找不到引用者（`.text` 里没有 `B0 41 9A 00`，Ghidra 的 xrefs 也返回空），
   但这不等于运行时不用——reloc 存在就说明链接器认为它们是地址。

零收益 + 不确定风险，按**不翻**处理。同理的还有地形枚举（`WATER`/`RIVER`）
和配置键名（`SoundFx`），那两类已经由 `check_danger.py` 挡住。

用法
----
    python tools/save_sections.py           # 列出并对比词典
    python tools/save_sections.py --fix     # 从 zh_dict.json 删除这些条目
    python tools/save_sections.py --csv x.csv
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

MIN_LEN = 2          # 段名最短长度
MIN_RUN = 20         # 连续多少项才算"表"
MAX_NAME = 40        # 段名最长长度（更长的多半不是段名）


def parse_pe(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    base = struct.unpack_from("<I", blob, pe + 24 + 28)[0]
    table = pe + 24 + optsz
    segs = {}
    for i in range(nsec):
        o = table + i * 40
        name = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        vs, rva, rs, ra = struct.unpack_from("<IIII", blob, o + 8)
        segs[name] = {"rva": rva, "file_offset": ra, "raw_size": rs}
    return base, segs


def sec_name_strings(blob, rdata, base):
    """扫 .rdata，找出"连续指向小写标识符串"的最长指针表

    注意坐标：表里存的是 **VA**（如 0x9A4824），而段表给的是 **RVA**。
    转文件偏移必须走 `file_offset + (va - base - rva)`，
    直接 va - rva 会多算一个 image_base。
    """
    lo = rdata["rva"]
    hi = lo + rdata["raw_size"]
    off = rdata["file_offset"]
    n = rdata["raw_size"]

    def name_at(va):
        o = off + (va - base - lo)
        if o <= 0 or o + 2 > len(blob):
            return None
        s = b""
        while o + 1 < len(blob):
            w = blob[o] | (blob[o + 1] << 8)
            if w == 0:
                break
            if not (32 <= w < 127):
                return None
            s += bytes([w])
            o += 2
            if len(s) > MAX_NAME * 2:
                return None
        if not (MIN_LEN <= len(s) <= MAX_NAME):
            return None
        t = s.decode("ascii")
        # 段名特征：小写开头，允许空格/括号/连字符，不含大写
        if not t[0].islower() or t != t.lower():
            return None
        return t

    best = (0, 0, [])
    i = 0
    while i <= n - 4:
        run = []
        j = i
        while j <= n - 4:
            v = struct.unpack_from("<I", blob, off + j)[0]
            if not (base + lo <= v < base + hi):
                break
            t = name_at(v)
            if t is None:
                break
            run.append((base + lo + j, t))
            j += 4
        if len(run) > len(best[2]):
            best = (lo + i, j, run)
        i = j + 4 if j > i else i + 4
    start, end, run = best
    if len(run) < MIN_RUN:
        return None, []
    return start, run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--dict", default=os.path.join(ROOT, "zh_dict.json"))
    ap.add_argument("--fix", action="store_true", help="从词典删除这些条目")
    ap.add_argument("--csv")
    args = ap.parse_args()

    if not os.path.exists(args.exe):
        sys.exit("找不到 %s\n先跑 python tools/make_decrypted_exe.py" % args.exe)

    blob = open(args.exe, "rb").read()
    base, segs = parse_pe(blob)
    if ".rdata" not in segs:
        sys.exit("exe 没有 .rdata")

    start, run = sec_name_strings(blob, segs[".rdata"], base)
    if not run:
        sys.exit("没找到存档段名表（游戏更新过？阈值需要调）")

    print("存档段名表: %#x .. %#x  共 %d 项  (VA, image base %#x)"
          % (base + start, base + start + len(run) * 4 - 4, len(run), base))

    dic = json.load(open(args.dict, encoding="utf-8"))
    patched = set()
    pf = os.path.join(os.environ.get("WITE2_GAME", ""), "wite2_patch.txt")
    if os.path.exists(pf):
        for ln in open(pf, encoding="utf-8", errors="replace"):
            p = ln.split("\t")
            if len(p) >= 3:
                patched.add(p[1])

    rows = []
    for va, name in run:
        zh = dic.get(name)
        rows.append(
            {
                "name": name,
                "va": hex(va),
                "in_dict": "Y" if zh is not None else "",
                "zh": zh or "",
                "patched": "Y" if name in patched else "",
            }
        )

    hit = [r for r in rows if r["in_dict"]]
    done = [r for r in hit if r["patched"]]
    for r in rows:
        flag = ""
        if r["patched"] == "Y":
            flag = "  [已改写!]"
        elif r["in_dict"]:
            flag = "  [词典有,未命中]"
        print("  %-26s %s%s" % (r["name"], r["zh"] or "", flag))

    print()
    print("词典收录 %d / %d 条，其中已被补丁改写 %d 条" %
          (len(hit), len(rows), len(done)))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["name", "va", "in_dict", "zh", "patched"])
            w.writeheader()
            w.writerows(rows)
        print("写出 %s" % args.csv)

    if args.fix:
        if not hit:
            print("\n没有需要删除的条目")
            return
        for r in hit:
            del dic[r["name"]]
        with open(args.dict, "w", encoding="utf-8") as f:
            json.dump(dic, f, ensure_ascii=False, indent=1)
        print("\n已从 %s 删除 %d 条" % (args.dict, len(hit)))
        print("接着跑 build.bat assets 重新生成翻译表")
    elif hit:
        print("\n这些条目零收益且可能坏档。加 --fix 删除。")


if __name__ == "__main__":
    main()
