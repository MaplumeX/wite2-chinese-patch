#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用独立参考实现验证 dat_apply 的替换结果

为什么需要这个
--------------
明文里的字符串是 `[4字节字符数][UTF-16 正文]`。只要匹配不锚在记录边界上，
'Italian Campaign' 就会命中 'Italian Campaign Advances' 的前 16 个字符，
把它改成 '意大利战役' + 8 个空格 + 'dvances'。

那样产生的不是显示问题，是**崩溃**：这条名字随后要按名字去查 map，
查不到就抛 std::out_of_range，MSVC 上表现为未捕获 C++ 异常 E06D7363，
玩家看到的是"进入剧本 runtime error"。

这个脚本用 Python 重写一遍同样的规则（含码点白名单），跟 C 的输出
逐字节比对。不一致就说明 C 侧改了而这里没跟上，或者规则本身被破坏了。

用法:
    python tools/verify_dat_anchor.py <scn或dat> <map.tsv> <C输出明文>
"""

import io
import struct
import sys

sys.path.insert(0, __file__.rsplit("tools", 1)[0] + "tools")
from dat_crypto import KEY, rc4  # noqa: E402

MAX_CHARS = 40000


def cp_ok(c):
    return (32 <= c < 0x7f or 0xa0 <= c <= 0x24f or 0x2010 <= c <= 0x201f
            or 0x3000 <= c <= 0x303f or 0x4e00 <= c <= 0x9fff
            or 0xff00 <= c <= 0xffef)


def load_map(path):
    m = {}
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or "\t" not in line:
                continue
            k, v = line.rstrip("\n").split("\t", 1)
            m[k] = v
    return m


def reference(plain, m):
    buf = bytearray(plain)
    n = len(buf)
    hits = 0
    for parity in (0, 1):
        i = 4 + parity
        while i + 4 < n:
            L = struct.unpack("<I", plain[i - 4:i])[0]
            if not (1 <= L <= MAX_CHARS) or i + L * 2 > n:
                i += 2
                continue
            if not all(cp_ok(plain[i + t * 2] | (plain[i + t * 2 + 1] << 8))
                       for t in range(L)):
                i += 2
                continue
            t = plain[i:i + L * 2].decode("utf-16-le")
            if t in m and len(m[t]) <= L:
                buf[i:i + L * 2] = (m[t] + " " * (L - len(m[t]))).encode("utf-16-le")
                hits += 1
            i += L * 2
    return bytes(buf), hits


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    src, mapfile, c_out = sys.argv[1:4]
    with open(src, "rb") as f:
        plain = __import__("zlib").decompress(rc4(KEY, f.read()), 31)
    m = load_map(mapfile)
    ref, hits = reference(plain, m)
    with open(c_out, "rb") as f:
        got = f.read()
    print("参考实现命中 %d 处" % hits)
    print("与 C 输出逐字节一致: %s" % (ref == got))
    if ref != got:
        d = [x for x in range(min(len(ref), len(got))) if ref[x] != got[x]]
        print("  差异 %d 字节，首处 0x%X" % (len(d), d[0]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
