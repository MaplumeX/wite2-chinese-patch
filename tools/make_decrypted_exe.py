#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成"解密版 exe"给静态分析器用

游戏 exe 的 .text 段在磁盘上是加密的（熵 8.000），Ghidra / IDA 直接打开
只能看到垃圾指令。但运行时内存里是明文，dump 出来（wite2_text.bin）后
与磁盘 .text 的 VirtualSize 完全等长（0x50A983）。

本脚本把 dump 的明文 .text 写回 PE 的 .text 原始数据区，其余段、段表、
重定位、绑定导入一个字节都不动。产物是一个结构完全合法的 PE32，
Ghidra 能正常建函数、建交叉引用。

注意：产物只用于**静态分析**，绝不能拿去运行（.text 已明文，
游戏自身的解密步骤会把明文再"解密"一次，必然崩溃）。

用法
----
    python tools/make_decrypted_exe.py
    python tools/make_decrypted_exe.py --check      # 只校验，不写
"""

import argparse
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
DUMP = os.path.join(GAME, "wite2_text.bin")
OUT = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")


def sections(blob):
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    table = pe + 24 + optsz
    out = []
    for i in range(nsec):
        o = table + i * 40
        name = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        vs, va, rs, ra = struct.unpack_from("<IIII", blob, o + 8)
        out.append({"o": o, "name": name, "vsize": vs, "va": va,
                    "raw_size": rs, "raw_ptr": ra})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写文件")
    args = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit("找不到游戏 exe: %s" % EXE)
    if not os.path.exists(DUMP):
        sys.exit("找不到代码段 dump: %s\n（用 tools/dump_code.c 编一个 DLL 在游戏里 dump 一次）" % DUMP)

    blob = bytearray(open(EXE, "rb").read())
    dump = open(DUMP, "rb").read()

    for s in sections(blob):
        if s["name"] != ".text":
            continue
        print(".text  VSize=%#x  RawPtr=%#x  RawSize=%#x" % (
            s["vsize"], s["raw_ptr"], s["raw_size"]))
        print("dump  %#x 字节" % len(dump))
        if len(dump) != s["vsize"]:
            sys.exit("长度不等于 VirtualSize，dump 不完整（%#x != %#x）"
                     % (len(dump), s["vsize"]))
        if len(dump) > s["raw_size"]:
            sys.exit("dump 比 RawSize 还大，装不进去")
        if args.check:
            print("OK：可以直接写回")
            return
        rp = s["raw_ptr"]
        blob[rp:rp + len(dump)] = dump
        pad = s["raw_size"] - len(dump)
        blob[rp + len(dump):rp + s["raw_size"]] = b"\0" * pad
        print("尾部补零 %d 字节" % pad)
        break
    else:
        sys.exit("exe 里没有 .text 段？")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "wb").write(blob)
    print("写出 %s（%d 字节）" % (OUT, len(blob)))
    print("sha256 %s" % hashlib.sha256(blob).hexdigest())


if __name__ == "__main__":
    main()
