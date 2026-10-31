"""找出 .rdata 里「定步长排列的短串」—— 这类是查找表，不是显示文本。

判据
----
显示文本在 .rdata 里是连续紧凑排布的（一串接一串，中间没有填充）。
查找表不一样：它是一个结构体数组，每个元素里有一个字符串字段，
所以相邻字符串的起点间距是**恒定的小步长**（8/12/16/20/24/32/40 字节）。

例子（.rdata+0xC31D4 附近的国家代码表）：

  0C31D4  'CA'
  0C31DC  'IN'      间距 8
  0C31E4  'NW'
  ...

游戏拿这些串去拼路径 //Sym//%s//%s.tga 或做下标映射。改成中文后
拼出来的路径不存在，加载返回 NULL，之后按图片指针去画就崩 —— 而崩
的位置看起来完全无关（实测跳到 .rdata 里执行）。

输出：这些表的偏移范围，供后续决定是否纳入封印。
"""
import re
import struct

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = GAME + r"\WarInTheEast2.exe"

STEPS = (6, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 56, 64)
TOKEN = re.compile(r"^[A-Za-z0-9 .\-_/:'()%]{1,40}$")


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
    assert rptr is not None

    body = d[rptr:rptr + rsz]

    # 收集所有 UTF-16 串：偏移 -> 文本
    strings = {}
    i = 0
    n = len(body)
    while i < n - 2:
        j = i
        ch = []
        while j < n - 2 and body[j + 1] == 0 and 32 <= body[j] < 127:
            ch.append(chr(body[j]))
            j += 2
        if len(ch) >= 1:
            strings[i] = "".join(ch)
            i = j + 2 if j + 2 <= n else n
        else:
            i += 2

    offs = sorted(strings)

    # 找定步长链
    tables = []
    seen = set()
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
                tables.append((step, chain))
                seen.update(chain)

    if not tables:
        print("没有找到定步长表")
        return

    # 去重、按起始偏移排序
    tables.sort(key=lambda t: t[1][0])
    print("定步长短串表：%d 个，覆盖 %d 个串"
          % (len(tables), len(set(x for _, c in tables for x in c))))
    print()

    import json
    raw = json.load(open("zh_dict.json", encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))

    risky = set()
    for step, chain in tables:
        texts = [strings[x] for x in chain]
        # 只关心会被改写的
        trans = [(x, strings[x]) for x in chain
                 if strings[x] in raw and strings[x] not in draw]
        if not trans:
            continue
        print("步长 %2d  起始 .rdata+%06X  %d 个串  其中 %d 个会被改写"
              % (step, chain[0], len(chain), len(trans)))
        print("   样例: %s" % ", ".join(repr(t) for t in texts[:8]))
        print("   改写: %s" % ", ".join(repr(t) for _, t in trans[:8]))
        for _, t in trans:
            risky.add(t)
        print()

    print("落入定步长表、且正在被改写的条目：%d 条" % len(risky))
    with open("_table_hits.txt", "w", encoding="utf-8") as f:
        for t in sorted(risky):
            f.write(t + "\n")
    print("已写 _table_hits.txt")


if __name__ == "__main__":
    main()
