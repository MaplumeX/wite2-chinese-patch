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


def find_utf16_strings(plain, min_chars=3, ascii_only=False):
    """扫描 UTF-16LE 字符串，**奇偶两种对齐都扫**。

    为什么必须扫两种对齐
    --------------------
    `.dat` 里的字符串多在偶数偏移，但 `.scn` 里很多在奇数偏移
    （实测 "Situation Report" 在 0x634B7FD）。只扫偶数偏移会得到
    "0 处命中"，看起来像词典没对上，其实一个字符串都没看见。

    但两种对齐都扫会带来一个问题：把一段真字符串按**错位**读，字节
    反序后落进中日韩码点范围（例如 "continuation" 反读成
    '漀渀琀椀渀甀愀琀椀漀渀'），看起来也是"合法字符串"。

    所以加了 `ascii_only` 开关：只要纯 ASCII 的候选（即真正的英文
    原文），反序乱码会被自然滤掉。
    """
    out = []
    n = len(plain)
    for parity in (0, 1):
        i = HEADER_LEN + parity
        if i % 2 != parity:
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
                t = "".join(ch)
                if any(x.isalpha() for x in t):
                    if ascii_only and not all(32 <= ord(x) < 127 for x in t):
                        pass
                    else:
                        out.append((i, t))
                i = j if j > i else i + 2
            else:
                i += 2
    # 去重（同一位置只保留一次），按偏移排序
    seen = set()
    uniq = []
    for off, t in out:
        if (off, t) in seen:
            continue
        seen.add((off, t))
        uniq.append((off, t))
    uniq.sort()
    return uniq


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
    ap.add_argument("--ascii-only", action="store_true",
                    help="只要纯 ASCII 串（滤掉错位反读的乱码）")
    args = ap.parse_args()

    names = ([args.name] if args.name else
             [os.path.basename(p)[:-4]
              for p in sorted(glob.glob(os.path.join(GENDATA, "*.dat")))])

    if not args.name:
        print("%-22s %8s %8s  %s" % ("文件", "明文", "串数", "最长"))
        total = 0
        for n in names:
            plain = load_dat(n)
            strs = find_utf16_strings(plain, args.min, args.ascii_only)
            total += len(strs)
            longest = max((len(s) for _, s in strs), default=0)
            print("%-22s %8d %8d  %d" % (n + ".dat", len(plain), len(strs),
                                         longest))
        print("\n合计 %d 条" % total)
        return 0

    plain = load_dat(args.name)
    strs = find_utf16_strings(plain, args.min, args.ascii_only)

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
