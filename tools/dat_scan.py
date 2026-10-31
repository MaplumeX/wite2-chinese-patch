#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 .dat 明文里的 UTF-16 字符串，导出成待翻译清单

为什么需要严格对齐
------------------
`.dat` 明文是二进制结构，里面既有 UTF-16 字符串也有大量二进制字段。
如果按"逐字节尝试 UTF-16LE"扫，奇数偏移会把字节反序当成字符，扫出
`'匀椀渀挀攀'`（"since" 的反序）这种垃圾。所以只从**偶数偏移**起扫，
并且要求：

  * 字符在合理码点范围内（拉丁补充 / 中日韩 / 全角标点 / 通用标点）
  * 整串至少 min 个字符，且至少含一个字母

这样扫 `w_gameEvent.dat` 得到 6343 条，其中 63 条是 >200 字符的事件正文。

用法
----
    python tools/dat_scan.py                     # 全部文件摘要
    python tools/dat_scan.py w_gameEvent         # 单文件明细
    python tools/dat_scan.py w_gameEvent --min 60 --json out.json
    python tools/dat_scan.py --todo w_gameEvent  # 导出待译清单（含中文对照）
"""

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from dat_crypto import GENDATA, OUTDIR, dat_decrypt

HEADER_LEN = 0x100          # _2by3 头长度


def _ok_cp(c):
    return (32 <= c < 0x7f                       # ASCII 可打印
            or 0xa0 <= c <= 0x24f                # 拉丁补充
            or 0x2010 <= c <= 0x201f             # 通用标点（引号/破折号）
            or 0x3000 <= c <= 0x303f             # 中日韩标点
            or 0x4e00 <= c <= 0x9fff             # 中日韩汉字
            or 0xff00 <= c <= 0xffef)            # 全角


def find_utf16_strings(plain, min_chars=3):
    """返回 [(offset, text)]。从偶数偏移起扫，严格码点校验。"""
    out = []
    n = len(plain)
    i = HEADER_LEN
    if i % 2:
        i += 1
    while i + 3 < n:
        ch = []
        j = i
        while j + 1 < n:
            c = plain[j] | (plain[j + 1] << 8)
            if c == 0:
                break
            if not _ok_cp(c):
                break
            ch.append(chr(c))
            j += 2
        if len(ch) >= min_chars:
            s = "".join(ch)
            if any(x.isalpha() for x in s):
                out.append((i, s))
            i = j if j > i else i + 2
        else:
            i += 2
    return out


def load_dat(name):
    """优先读 build/dat/ 里已解包的文件，没有再现场解密。"""
    if name.endswith(".dat"):
        name = name[:-4]
    fname = name + ".dat"
    local = os.path.join(OUTDIR, fname)
    if os.path.exists(local):
        return open(local, "rb").read()
    src = os.path.join(GENDATA, fname)
    return dat_decrypt(open(src, "rb").read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="不填则扫全部")
    ap.add_argument("--min", type=int, default=3, help="最短字符数")
    ap.add_argument("--json", default=None, help="导出到 JSON")
    ap.add_argument("--todo", action="store_true",
                    help="只列还没进 dat_trans 的条目")
    args = ap.parse_args()

    names = ([args.name] if args.name else
             [os.path.basename(p)[:-4]
              for p in sorted(glob.glob(os.path.join(GENDATA, "*.dat")))])

    if not args.name:
        print("%-22s %8s %8s  %s" % ("文件", "明文", "串数", "最长"))
        total = 0
        for n in names:
            plain = load_dat(n)
            strs = find_utf16_strings(plain, args.min)
            total += len(strs)
            longest = max((len(s) for _, s in strs), default=0)
            print("%-22s %8d %8d  %d" % (n + ".dat", len(plain), len(strs),
                                         longest))
        print("\n合计 %d 条" % total)
        return 0

    plain = load_dat(args.name)
    strs = find_utf16_strings(plain, args.min)

    done = {}
    tp = os.path.join(ROOT, "dat_trans", args.name + ".json")
    if os.path.exists(tp):
        with open(tp, encoding="utf-8") as f:
            done = json.load(f)

    if args.todo:
        todo = [(o, s) for o, s in strs if s not in done]
        print("%s: %d 条字符串，已翻 %d，待译 %d"
              % (args.name, len(strs), len(done), len(todo)))
        for o, s in todo:
            print("  0x%06x [%3d] %s" % (o, len(s), s[:160]))
    else:
        print("%s: 明文 %d 字节，%d 条字符串\n"
              % (args.name, len(plain), len(strs)))
        for o, s in strs:
            mark = "✓" if s in done else " "
            print("  %s 0x%06x [%4d] %s" % (mark, o, len(s), s[:160]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([{"offset": o, "text": s} for o, s in strs], f,
                      ensure_ascii=False, indent=1)
        print("\n已导出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
