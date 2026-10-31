"""定步长表 × 真实作用域：找出真正会被改写的那些。

上一版直接用词典，把「只绘制层」的条目也算进来了（'de'、'&' 其实
已经不改内存），报出 1798 个表，没用。
这里只认 TSV 里作用域为空/d/b 的条目 —— 也就是**真的会写进内存**的。
"""
import json
import re
import struct

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = GAME + r"\WarInTheEast2.exe"

STEPS = (8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 56, 64)


def main():
    d = open(EXE, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    img = struct.unpack_from("<I", d, pe + 52)[0]
    base = pe + 24 + optsz
    rva = rsz = rptr = None
    for i in range(nsec):
        o = base + i * 40
        nm = d[o:o + 8].rstrip(b"\0").decode("latin1")
        if nm != ".rdata":
            continue
        va = struct.unpack_from("<I", d, o + 12)[0]
        rsz = struct.unpack_from("<I", d, o + 16)[0]
        rptr = struct.unpack_from("<I", d, o + 20)[0]
        rva = img + va
    body = d[rptr:rptr + rsz]

    strings = {}
    i = 0
    n = len(body)
    while i < n - 2:
        j = i
        ch = []
        while j < n - 2 and body[j + 1] == 0 and 32 <= body[j] < 127:
            ch.append(chr(body[j]))
            j += 2
        if ch:
            strings[i] = "".join(ch)
            i = j + 2 if j + 2 <= n else n
        else:
            i += 2
    offs = sorted(strings)

    # 真实作用域
    mem = set()
    for line in open("wite2_zh.tsv", encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 2:
            continue
        sc = p[2] if len(p) > 2 else ""
        if sc in ("t", "x"):
            continue
        if sc == "d":
            continue      # 只改 .data
        mem.add(p[0])

    seen = set()
    rows = []
    for step in STEPS:
        for a in offs:
            if a in seen:
                continue
            chain = [a]
            cur = a
            while True:
                nxt = cur + step
                if nxt not in strings:
                    break
                if len(strings[nxt]) * 2 + 2 > step:
                    break
                chain.append(nxt)
                cur = nxt
            if len(chain) >= 5:
                seen.update(chain)
                hit = [strings[x] for x in chain if strings[x] in mem]
                if hit:
                    rows.append((chain[0], step, len(chain), hit,
                                 [strings[x] for x in chain]))

    rows.sort()
    print("会被改写、且处在定步长表里的串：%d 个表 / %d 条"
          % (len(rows), len(set(t for _, _, _, h, _ in rows for t in h))))
    print()
    for start, step, count, hit, sample in rows:
        print(".rdata+%06X  步长%2d  %d 串  改写 %d 条  %s"
              % (start, step, count, len(hit), ", ".join(repr(t) for t in hit[:6])))
        print("    表内容: %s" % ", ".join(repr(t) for t in sample[:10]))


if __name__ == "__main__":
    main()
