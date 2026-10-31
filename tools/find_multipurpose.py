"""找出既被当作显示文本、又被当作查找键的字符串。

判据：同一个串在 exe 里出现多次。出现一次基本只是显示文本；
出现两处以上，说明它还有第二个用途 —— 比较常见的是枚举成员、
配置键、消息队列类型名。改了以后游戏拿它去查表就查不到，返回
NULL 再解引用就是崩溃。

真实事故：Message / Messages 各在 .rdata 出现三次，属于通讯系统的
消息类型标识。改成"消息"后点"通讯报告"直接崩。short_class() 只挡
6 字符以内的串，7 字符的 Message 刚好漏过去。
"""
import json
import os
import sys

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV = os.path.join(ROOT, "wite2_zh.tsv")
DICT = os.path.join(ROOT, "zh_dict.json")


def occurrences(exe, s):
    """返回该串作为 UTF-16 独立串出现的偏移列表。"""
    b = s.encode("utf-16-le")
    hits = []
    i = 0
    while True:
        i = exe.find(b, i)
        if i < 0:
            return hits
        pre_ok = (i == 0) or exe[i - 2:i] == b"\x00\x00"
        post = exe[i + len(b):i + len(b) + 2]
        post_ok = (len(post) < 2) or (post == b"\x00\x00")
        if pre_ok and post_ok:
            hits.append(i)
        i += 1


def main():
    only_mem = "--mem" in sys.argv
    exe = open(EXE, "rb").read()
    raw = json.load(open(DICT, encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))

    rows = []
    for line in open(TSV, encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 2:
            continue
        k, v = p[0], p[1]
        sc = p[2] if len(p) > 2 else ""
        if sc in ("t", "x"):
            continue
        if k in draw:
            continue
        rows.append(k)

    risky = []
    for k in rows:
        if k.startswith("%"):
            continue
        n = len(occurrences(exe, k))
        if n >= 2:
            risky.append((n, k))

    risky.sort(reverse=True)
    print("会写内存的条目 %d 条，其中在 exe 出现 2 次以上的 %d 条"
          % (len(rows), len(risky)))
    print()
    for n, k in risky:
        print("  %d 次  %r" % (n, k))
    if only_mem:
        return
    open(os.path.join(ROOT, "_risky_multi.txt"), "w",
         encoding="utf-8").write(
        "\n".join("%d\t%s" % (n, k) for n, k in risky) + "\n")
    print()
    print("已写 _risky_multi.txt")


if __name__ == "__main__":
    main()
