"""一次性审计：还有没有会写进 .rdata 的「表项」。

合并两项检查，任何一项不为 0 就说明有回归：

1. 固定步长表（字符表 / locale 表 / 结构体数组里的字符串字段）
   这类串被游戏按结构体或下标访问，改成中文会错位。

2. 按字母排序的块（二分查找的键）
   改了其中一条就破坏排序，查找落到错误的元素上。

这两类都曾导致崩溃：'&' -> '与' 命中标点表，月份名命中日期表，
'spanish-*' 之类命中排序表。修复后应当恒为 0。

用法：
    python tools/audit_rdata.py           只报告
    python tools/audit_rdata.py --fix     就地把命中的条目降到绘制层
"""
import json
import os
import struct
import sys

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT = os.path.join(ROOT, "zh_dict.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

STEPS = (8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 56, 64)
MIN_CHAIN = 5
MIN_SORTED = 6


def load():
    d = open(EXE, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    base = pe + 24 + optsz
    for i in range(nsec):
        o = base + i * 40
        if d[o:o + 8].rstrip(b"\0") != b".rdata":
            continue
        rsz = struct.unpack_from("<I", d, o + 16)[0]
        rptr = struct.unpack_from("<I", d, o + 20)[0]
        return d[rptr:rptr + rsz]
    raise SystemExit("找不到 .rdata")


def strings_of(body):
    out = {}
    i = 0
    n = len(body)
    while i < n - 2:
        j = i
        ch = []
        while j < n - 2 and body[j + 1] == 0 and 32 <= body[j] < 127:
            ch.append(chr(body[j]))
            j += 2
        if ch:
            out[i] = "".join(ch)
            i = j + 2 if j + 2 <= n else n
        else:
            i += 2
    return out


def mem_entries():
    s = set()
    for line in open(TSV, encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 2:
            continue
        sc = p[2] if len(p) > 2 else ""
        if sc in ("t", "x", "d"):
            continue
        s.add(p[0])
    return s


def find_stride(strings, mem):
    offs = sorted(strings)
    seen = set()
    hits = set()
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
            if len(chain) >= MIN_CHAIN:
                seen.update(chain)
                for x in chain:
                    if strings[x] in mem:
                        hits.add((strings[x], "步长%d@%06X" % (step, chain[0])))
    return hits


def find_sorted(strings, mem):
    blocks = []
    cur = []
    for off in sorted(strings):
        if cur and cur[-1][0] + len(cur[-1][1]) * 2 + 2 == off:
            cur.append((off, strings[off]))
        else:
            if len(cur) >= MIN_SORTED:
                blocks.append(cur)
            cur = [(off, strings[off])]
    if len(cur) >= MIN_SORTED:
        blocks.append(cur)

    hits = set()
    for blk in blocks:
        texts = [t for _, t in blk]
        low = [t.lower() for t in texts]
        if low != sorted(low):
            continue
        for t in texts:
            if t in mem:
                hits.add((t, "排序块@%06X" % blk[0][0]))
    return hits


def main():
    body = load()
    strings = strings_of(body)
    mem = mem_entries()
    print(".rdata 字符串 %d 个，会写内存的条目 %d 条" % (len(strings), len(mem)))

    stride = find_stride(strings, mem)
    srt = find_sorted(strings, mem)

    print()
    print("步长表命中: %d" % len(stride))
    for t, where in sorted(stride):
        print("   %-30r %s" % (t, where))
    print("排序表命中: %d" % len(srt))
    for t, where in sorted(srt):
        print("   %-30r %s" % (t, where))

    total = {t for t, _ in stride} | {t for t, _ in srt}
    if not total:
        print()
        print("OK: 没有表项会被改写")
        return 0

    if "--fix" in sys.argv:
        raw = json.load(open(DICT, encoding="utf-8"))
        draw = raw.setdefault("_drawOnly", [])
        have = set(draw)
        n = 0
        for t in sorted(total):
            if t in raw and t not in have:
                draw.append(t)
                have.add(t)
                n += 1
        json.dump(raw, open(DICT, "w", encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=1)
        print()
        print("已降级 %d 条，请重新 build_assets 后复跑" % n)
        return 0

    print()
    print("需要降级 %d 条；加 --fix 自动处理" % len(total))
    return 1


if __name__ == "__main__":
    sys.exit(main())
