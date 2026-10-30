#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量检查翻译残留

对游戏所有已知界面字符串模拟翻译, 找出翻译后仍残留的英文单词,
按出现频次排序, 用于批量补齐词典。

用法:
    python tools/find_gaps.py            列出高频残留词
    python tools/find_gaps.py --detail   列出每条残留的完整上下文
    python tools/find_gaps.py --words    只输出缺失词列表(便于批量翻译)
"""

import glob
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import test_match as T  # noqa: E402

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"

# 明显不是界面文本的技术串
TECH = re.compile(
    r"^(Content|If-|Accept|Cache|Connection|ETag|Expires|From$|Host|"
    r"Authorization|Allow|Date$|Range|Referer|Server|Set-|Transfer-|"
    r"Upgrade|Vary|Via|Warning|WWW-|Proxy-|Max-|Retry-|Trailer|"
    r"Location|Pragma|Cookie|Link$|Origin|Access-|X-|Expect|Do-Not|"
    r"Keep-Alive|Last-|User-Agent|application/|text/|multipart/|image/)")

CODE = re.compile(
    r"(\.cpp|\.h$|\.dll|\.dat|\.csv|\.txt|\.log|\.json|\.tga|\.scn|\.ttf|"
    r"%[-0-9.*]*[sdifxc]|0x[0-9a-f]{4,}|==|->|::|\{|\}|\[|\\|http|www\.|"
    r"D3DUtil|LockRect|DrawTextScaled|Render3DText|InitDevice|"
    r"RestoreDevice|GetBackBuffer|JSON|XML|charset|octet-stream)")


def load_exe_strings():
    """从 exe .rdata 提取 UTF-16 字符串, 只取界面区段"""
    exe = os.path.join(GAME, "WarInTheEast2.exe")
    if not os.path.exists(exe):
        return []
    d = open(exe, "rb").read()
    e = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, e + 6)[0]
    optsz = struct.unpack_from("<H", d, e + 20)[0]
    secs = []
    for i in range(nsec):
        o = e + 24 + optsz + 40 * i
        name = d[o:o + 8].rstrip(b"\0").decode()
        vs, va, rs, ra = struct.unpack_from("<IIII", d, o + 8)
        secs.append((name, va, vs, ra, rs))
    rd = next(s for s in secs if s[0] == ".rdata")
    rd_va, rd_raw, rd_sz = rd[1], rd[3], rd[4]
    body = d[rd_raw:rd_raw + rd_sz]

    # 界面文本集中在 0x548000-0x5C6000
    lo, hi = 0x548000, 0x5C6000
    sub = body[lo - rd_va:hi - rd_va]
    pat = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")
    out, seen = [], set()
    for m in pat.finditer(sub):
        s = m.group().decode("utf-16-le")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_drawn_strings():
    p = os.path.join(GAME, "wite2_strings.txt")
    if not os.path.exists(p):
        return []
    raw = open(p, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    out, seen = [], set()
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        s = line.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def analyze():
    entries = T.load_entries()
    cands = load_exe_strings() + load_drawn_strings()

    gaps = []      # (原文, 译文, 残留词列表)
    wordfreq = {}

    for s in cands:
        if len(s) < 3 or len(s) > 90:
            continue
        if TECH.match(s) or CODE.search(s):
            continue
        if not re.search(r"[A-Za-z]{2,}", s):
            continue

        got, _ = T.dt_check(s, entries)
        # 残留英文单词(>=3 字母, 排除全大写缩写如 OKH/LI/PG2)
        words = [w for w in re.findall(r"[A-Za-z]{3,}", got)
                 if not re.fullmatch(r"[A-Z]{2,6}", w)]
        if not words:
            continue

        gaps.append((s, got, words))
        for w in words:
            wordfreq[w.lower()] = wordfreq.get(w.lower(), 0) + 1

    return gaps, wordfreq


def main():
    gaps, wordfreq = analyze()

    if "--words" in sys.argv:
        for w, c in sorted(wordfreq.items(), key=lambda x: -x[1]):
            print("%s\t%d" % (w, c))
        return 0

    print("=== 翻译后仍残留英文的串: %d 条 ===\n" % len(gaps))
    print("=== 高频残留词 (按出现次数) ===\n")
    for w, c in sorted(wordfreq.items(), key=lambda x: -x[1])[:80]:
        print("  %-18s %d" % (w, c))

    if "--detail" in sys.argv:
        print("\n=== 明细 (前 120 条) ===\n")
        for s, got, words in gaps[:120]:
            print("  %-44s -> %-30s 缺:%s"
                  % (s[:44], got[:30], ",".join(words[:4])))

    return 0


if __name__ == "__main__":
    sys.exit(main())
