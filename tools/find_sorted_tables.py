"""找出 .rdata 里「按字母顺序排列」的字符串块。

如果游戏对某个字符串表做二分查找，表必须是排好序的。我们翻译了其中
几条，顺序被破坏，二分就会落到错误的元素上 —— 返回的不是记录指针而是
别的东西，再按结构体偏移去写就崩。这符合观察到的现象：

  EIP 落在 .rdata 里（拿到了一个不是函数的地址去调用）
  access=WRITE，target = eax+0x3C（把字符串当结构体，写 0x3C 偏移处）
  只有改 .rdata 才崩，只改 .data 不崩

统计意义：这类表是「查表用的键」，不能翻译。
"""
import json
import os
import re
import struct

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    d = open(EXE, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    base = pe + 24 + optsz
    rsz = rptr = None
    for i in range(nsec):
        o = base + i * 40
        if d[o:o + 8].rstrip(b"\0") != b".rdata":
            continue
        rsz = struct.unpack_from("<I", d, o + 16)[0]
        rptr = struct.unpack_from("<I", d, o + 20)[0]
    body = d[rptr:rptr + rsz]

    # 拆分：连续紧凑排布的字符串视为一个块
    blocks = []
    cur = []
    i = 0
    n = len(body)
    while i < n - 2:
        j = i
        ch = []
        while j < n - 2 and body[j + 1] == 0 and 32 <= body[j] < 127:
            ch.append(chr(body[j]))
            j += 2
        if ch:
            text = "".join(ch)
            nxt = j + 2
            if cur and cur[-1][0] + len(cur[-1][1]) * 2 + 2 == i:
                cur.append((i, text))
            else:
                if len(cur) >= 6:
                    blocks.append(cur)
                cur = [(i, text)]
            i = nxt if nxt <= n else n
        else:
            if len(cur) >= 6:
                blocks.append(cur)
            cur = []
            i += 2
    if len(cur) >= 6:
        blocks.append(cur)

    mem = set()
    for line in open(os.path.join(ROOT, "wite2_zh.tsv"), encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 2:
            continue
        sc = p[2] if len(p) > 2 else ""
        if sc in ("t", "x", "d"):
            continue
        mem.add(p[0])

    raw = json.load(open(os.path.join(ROOT, "zh_dict.json"), encoding="utf-8"))

    suspicious = []
    for blk in blocks:
        texts = [t for _, t in blk]
        if len(texts) < 6:
            continue
        # 排好序？（忽略大小写）
        low = [t.lower() for t in texts]
        if low != sorted(low):
            continue
        hit = [t for t in texts if t in mem]
        if not hit:
            continue
        suspicious.append((blk[0][0], len(texts), hit, texts))

    print("按字母排序、且含会被改写的条目的字符串块：%d 个" % len(suspicious))
    total = set()
    for start, count, hit, texts in suspicious:
        print()
        print(".rdata+%06X  %d 串  改写 %d 条" % (start, count, len(hit)))
        print("    %s" % ", ".join(repr(t) for t in texts[:12]))
        print("    改写: %s" % ", ".join(repr(t) for t in hit[:12]))
        total.update(hit)

    print()
    print("合计 %d 条需要降级:" % len(total))
    for t in sorted(total):
        print("   %-30r -> %r" % (t, raw[t]))

    if total:
        with open(os.path.join(ROOT, "_sorted_hits.txt"), "w",
                  encoding="utf-8") as f:
            for t in sorted(total):
                f.write(t + "\n")
        print()
        print("已写 _sorted_hits.txt")


if __name__ == "__main__":
    main()
