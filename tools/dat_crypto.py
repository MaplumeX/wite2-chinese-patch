#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2by3 引擎 .dat 文件的解包 / 重打包

格式（WITE2 / WITW 共用）
------------------------
    RC4( MD5("W17VVI$G0ODG4M3") , 文件全部字节 )  ->  gzip 流

也就是说**整个文件**先被 gzip 压缩，再整体用 RC4 加密，没有头部、没有
分段、没有长度字段。所以：

  * 46133 不是 16 的倍数也没关系 —— RC4 是流密码，按字节异或
  * 解密后头部一定是 `1f 8b 08 00`（gzip magic）
  * 明文头部是 `_2by3 WITE2_SF\\0`（8 字节标签 + 若干 0）

这个结论来自 33arc/WiTXtool 的源码（src/crypto.c + src/compress.c），
不是我逆向猜的 —— 密码字面量就写在那份源码里。

为什么重要
----------
之前一直认为 `.dat` 是"不可解的加密二进制"，只能靠 `Dat/CSV/*.csv` 反推。
实际不是：`.dat` 解开后是**明文 UTF-16**，事件正文、部队描述、装备名称
全在里面，而且和 CSV 里的内容逐字一致。

这意味着可以走**数据层**翻译：改明文 → 重新打包 → 交给游戏。整句替换，
不受绘制层"整串精确匹配"的约束，也不会出现
`the invasion of the 苏联 Union began` 这种半中半英。

用法
----
    python tools/dat_crypto.py list                    # 列出所有 .dat
    python tools/dat_crypto.py extract w_gameEvent     # 解包到 build/dat/
    python tools/dat_crypto.py extract-all
    python tools/dat_crypto.py repack build/dat/w_gameEvent.dat w_gameEvent
    python tools/dat_crypto.py verify                  # 全量往返自检
"""

import argparse
import glob
import hashlib
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GAME = os.environ.get(
    "WITE2_GAME",
    r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2")
GENDATA = os.path.join(GAME, "Dat", "GenData")
OUTDIR = os.path.join(ROOT, "build", "dat")

# 密码字面量来自 WiTXtool/src/crypto.c:rc4_operation()
PASSWORD = b"W17VVI$G0ODG4M3"
KEY = hashlib.md5(PASSWORD).digest()

GZIP_MAGIC = b"\x1f\x8b\x08\x00"
MAGIC2BY3 = b"_2by3 WITE2_SF"

# 明文布局：魔术头 + 填充 + 4 字节长度 + 1 字节 + 其余填充，正文从 0x100 起
PLAIN_TEXT_OFFSET = 0x100


def rc4(key, data):
    """标准 RC4。加密和解密是同一个操作。"""
    S = list(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + S[i] + key[i % klen]) & 0xFF
        S[i], S[j] = S[j], S[i]
    out = bytearray(len(data))
    x = y = 0
    for n, b in enumerate(data):
        x = (x + 1) & 0xFF
        y = (y + S[x]) & 0xFF
        S[x], S[y] = S[y], S[x]
        out[n] = b ^ S[(S[x] + S[y]) & 0xFF]
    return bytes(out)


def dat_decrypt(blob):
    """密文 -> 明文（gzip 解压后）。"""
    plain = zlib.decompress(rc4(KEY, blob), 31)
    return plain


def dat_encrypt(plain):
    """明文 -> 密文。压缩参数必须和游戏一致才能被它接受。"""
    # WiTXtool 用 Z_BEST_COMPRESSION(9) + gzip wrapper(15+16)
    comp = zlib.compress(plain, 9)
    # zlib.compress 产的是 zlib 包装(78 9c...)，游戏要 gzip(1f 8b)，
    # 所以自己用 compressobj 指定 wbits=31
    co = zlib.compressobj(9, zlib.DEFLATED, 31, 8, zlib.Z_DEFAULT_STRATEGY)
    comp = co.compress(plain) + co.flush()
    return rc4(KEY, comp)


def dat_text(plain):
    """从明文里取出真正的正文（跳过 _2by3 头）。"""
    return plain[PLAIN_TEXT_OFFSET:]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p_ex = sub.add_parser("extract")
    p_ex.add_argument("name")
    p_ex.add_argument("--out", default=None)

    sub.add_parser("extract-all")

    p_re = sub.add_parser("repack")
    p_re.add_argument("plain")
    p_re.add_argument("name")

    sub.add_parser("verify")

    args = ap.parse_args()

    if args.cmd == "list":
        files = sorted(glob.glob(os.path.join(GENDATA, "*.dat")))
        print("%-24s %10s  %10s  %s" % ("文件", "密文", "明文", "首 8 字节"))
        for f in files:
            blob = open(f, "rb").read()
            try:
                plain = dat_decrypt(blob)
                ok = "✓"
                n = len(plain)
                head = plain[:8]
            except Exception as e:
                ok = "✗ %s" % e
                n = 0
                head = b""
            print("%-24s %10d  %10s  %s" %
                  (os.path.basename(f), len(blob),
                   ("%d %s" % (n, ok)) if n else ok, head))
        return 0

    if args.cmd == "extract":
        name = args.name
        if not name.endswith(".dat"):
            name += ".dat"
        src = os.path.join(GENDATA, name)
        if not os.path.exists(src):
            print("找不到 %s" % src)
            return 1
        plain = dat_decrypt(open(src, "rb").read())
        os.makedirs(OUTDIR, exist_ok=True)
        out = args.out or os.path.join(OUTDIR, name)
        with open(out, "wb") as f:
            f.write(plain)
        print("%s -> %s (%d 字节明文)" % (name, out, len(plain)))
        if not plain.startswith(MAGIC2BY3):
            print("  警告: 明文不是以 %r 开头，可能有别的布局"
                  % MAGIC2BY3)
        return 0

    if args.cmd == "extract-all":
        os.makedirs(OUTDIR, exist_ok=True)
        files = sorted(glob.glob(os.path.join(GENDATA, "*.dat")))
        total = 0
        for f in files:
            name = os.path.basename(f)
            try:
                plain = dat_decrypt(open(f, "rb").read())
            except Exception as e:
                print("  ✗ %-24s %s" % (name, e))
                continue
            with open(os.path.join(OUTDIR, name), "wb") as g:
                g.write(plain)
            total += len(plain)
            print("  ✓ %-24s %8d 密文 -> %9d 明文" %
                  (name, os.path.getsize(f), len(plain)))
        print("\n共 %d 个文件，%d 字节明文 -> %s" % (len(files), total, OUTDIR))
        return 0

    if args.cmd == "repack":
        plain = open(args.plain, "rb").read()
        blob = dat_encrypt(plain)
        name = args.name
        if not name.endswith(".dat"):
            name += ".dat"
        os.makedirs(OUTDIR, exist_ok=True)
        out = os.path.join(OUTDIR, name)
        with open(out, "wb") as f:
            f.write(blob)
        print("%s -> %s (%d 字节密文)" % (args.plain, out, len(blob)))
        return 0

    if args.cmd == "verify":
        files = sorted(glob.glob(os.path.join(GENDATA, "*.dat")))
        print("往返自检：解密 -> 重新加密 -> 再解密，比对明文\n")
        bad = 0
        for f in files:
            name = os.path.basename(f)
            blob = open(f, "rb").read()
            try:
                p1 = dat_decrypt(blob)
            except Exception as e:
                print("  ✗ %-24s 解密失败 %s" % (name, e))
                bad += 1
                continue
            blob2 = dat_encrypt(p1)
            try:
                p2 = dat_decrypt(blob2)
            except Exception as e:
                print("  ✗ %-24s 重加密后解不开 %s" % (name, e))
                bad += 1
                continue
            same_plain = (p1 == p2)
            same_bytes = (blob == blob2)
            tag = "✓" if same_plain else "✗"
            if not same_plain:
                bad += 1
            print("  %s %-24s 明文一致=%s  密文逐字节一致=%s  (%d -> %d B)"
                  % (tag, name, same_plain, same_bytes, len(blob), len(blob2)))
        print("\n%s" % ("全部通过" if bad == 0 else "%d 个文件有问题" % bad))
        return 0 if bad == 0 else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
