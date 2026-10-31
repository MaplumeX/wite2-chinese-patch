#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 .dat 明文里做**等长**字符串替换，然后重新打包

为什么必须等长
--------------
`.dat` 明文是长度前缀 + 结构化记录（实测 `10 00 00 00` + "Situation Report"，
`f6 05 00 00` + 1526 字正文）。改长度会移动后面所有字节，而文件里还有
记录偏移/计数字段，一移就可能整体错位。

所以这里只做**原地等长替换**：

  * 译文用空格补到原字符数 → 文件字节数、所有偏移、所有长度前缀全不变
  * 中文一个字就是一个 UTF-16 码元，通常远比英文短，补空格绰绰有余
  * 英文原文保留在注释/映射表里，想回退直接跑 `--restore`

补空格而不是补别的，是因为游戏按空格分词排版 —— 尾部空格会被正常吸收，
不会画出来。

用法
----
    python tools/dat_patch.py --dry-run w_gameEvent
    python tools/dat_patch.py w_gameEvent
    python tools/dat_patch.py --restore w_gameEvent
"""

import argparse
import glob
import json
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from dat_crypto import GENDATA, OUTDIR, dat_decrypt, dat_encrypt

TRANS = os.path.join(ROOT, "dat_trans")

# 明文头部 0x100 字节是 _2by3 头，正文从后面开始；字符串都在正文区
HEADER_LEN = 0x100


def find_utf16_strings(plain, min_chars=3):
    """扫描 UTF-16LE 字符串，返回 [(offset, text)]。

    严格对齐：只接受从**偶数且前两字节为 0 或非文本**的位置开始，
    并要求整串至少 min_chars 个可打印字符。避免把奇数偏移的
    字节反序当成字符串（实测会出现 '匀椀渀挀攀' 这类乱码）。
    """
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
            if not (32 <= c < 0x7f or 0xa0 <= c <= 0x24f
                    or 0x4e00 <= c <= 0x9fff or 0x3000 <= c <= 0x303f
                    or 0xff00 <= c <= 0xffef or 0x2010 <= c <= 0x201f):
                break
            ch.append(chr(c))
            j += 2
        if len(ch) >= min_chars:
            s = "".join(ch)
            # 至少要有一个字母，排除纯数字/纯符号
            if any(x.isalpha() for x in s):
                out.append((i, s))
            i = j
        else:
            i += 2
    return out


def load_map(name):
    """读 dat_trans/<name>.json ： {英文原文: 译文}"""
    p = os.path.join(TRANS, name + ".json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def apply_map(plain, mapping, verbose=False):
    """等长原地替换。返回 (新明文, 命中数, 过长跳过列表, 未命中列表)"""
    buf = bytearray(plain)
    hits = 0
    too_long = []
    used = set()

    for off, src in find_utf16_strings(plain):
        dst = mapping.get(src)
        if dst is None:
            continue
        used.add(src)
        if len(dst) > len(src):
            too_long.append((src, dst, len(src), len(dst)))
            continue
        out = dst + " " * (len(src) - len(dst))   # 等长：补空格
        enc = out.encode("utf-16-le")
        assert len(enc) == len(src) * 2, (len(enc), len(src) * 2)
        buf[off:off + len(enc)] = enc
        hits += 1
        if verbose:
            print("    0x%06x  %r -> %r" % (off, src, out))

    missed = [k for k in mapping if k not in used]
    return bytes(buf), hits, too_long, missed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="例如 w_gameEvent（不带 .dat）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true",
                    help="从 build/dat_orig/ 恢复原文件")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--deploy", action="store_true",
                    help="重新打包并写入游戏目录（自动备份）")
    args = ap.parse_args()

    name = args.name
    if name.endswith(".dat"):
        name = name[:-4]
    fname = name + ".dat"
    game_path = os.path.join(GENDATA, fname)
    orig_dir = os.path.join(ROOT, "build", "dat_orig")
    os.makedirs(orig_dir, exist_ok=True)
    backup = os.path.join(orig_dir, fname)

    if args.restore:
        if not os.path.exists(backup):
            print("没有备份 %s" % backup)
            return 1
        shutil.copy2(backup, game_path)
        print("已恢复 %s" % game_path)
        return 0

    blob = open(game_path, "rb").read()
    plain = dat_decrypt(blob)

    # 首次修改前留一份原始密文备份
    if not os.path.exists(backup):
        with open(backup, "wb") as f:
            f.write(blob)
        print("已备份原文件 -> %s" % backup)

    mapping = load_map(name)
    print("%s: 明文 %d 字节，映射表 %d 条" % (fname, len(plain), len(mapping)))
    if not mapping:
        print("  没有 dat_trans/%s.json，无事可做" % name)
        return 1

    new_plain, hits, too_long, missed = apply_map(plain, mapping, args.verbose)

    print("  命中并替换 %d 处" % hits)
    if too_long:
        print("  ✗ 译文比原文长，跳过 %d 条：" % len(too_long))
        for s, d, a, b in too_long[:20]:
            print("      %r(%d) -> %r(%d)" % (s, a, d, b))
    if missed:
        print("  ⚠ 映射表里有 %d 条在本文件中没找到：" % len(missed))
        for m in missed[:10]:
            print("      %r" % m)

    if len(new_plain) != len(plain):
        print("  ✗ 大小变了，拒绝继续（等长替换不该改变大小）")
        return 1

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return 0

    new_blob = dat_encrypt(new_plain)
    out = os.path.join(OUTDIR, "patched_" + fname)
    with open(out, "wb") as f:
        f.write(new_blob)
    print("  重新打包 -> %s (%d 字节)" % (out, len(new_blob)))

    if args.deploy:
        shutil.copy2(out, game_path)
        print("  已部署到 %s" % game_path)
        print("  回退: python tools/dat_patch.py --restore %s" % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
