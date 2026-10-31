"""找出被当成程序标识符使用的字符串，而不是显示文本。

背景
----
patch_section 会把译文原地写回 .rdata / .data。如果那个串游戏另有用途
（JSON 字段名、配置键、消息类型），改了以后查找就失败，接着拿到 NULL
再解引用，就是崩溃。

真实事故：Message 改成"消息"后，点"通讯报告"必崩。它在 exe 里紧挨着
ErrorCode / DebugMessage / Password / Login /
authentication/steam/login/anonymous —— 是 Steam 登录响应的 JSON
字段名表。short_class() 只挡 6 字符以内的串，7 字符的 Message 刚好
漏过去。

判据
----
一个串如果被**程序**使用，它的邻居就是别的标识符：无空格、大小写成
规范、彼此紧挨（间距恰好等于串长加终止符）。显示文本不会这样排——
显示文本的邻居是句子、提示、带空格和标点的短语。

所以：看前后 0x80 字节内有几个「标识符邻居」。>= 2 个就认定这个串
属于标识符表，不能改内存。
"""
import json
import os
import re
import sys

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV = os.path.join(ROOT, "wite2_zh.tsv")
DICT = os.path.join(ROOT, "zh_dict.json")

# 形状像标识符：无空格、无标点句尾，首字母大写后全小写，或者全大写，
# 或者驼峰。不接受带空格或常见英文虚词的串。
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,30}$")


def iter_utf16(exe, lo, hi):
    """在 [lo, hi) 里逐串产出 (offset, text)。"""
    i = lo
    while i < hi - 4:
        j = i
        ch = []
        while j < len(exe) - 2 and exe[j + 1] == 0 and 32 <= exe[j] < 127:
            ch.append(chr(exe[j]))
            j += 2
        if len(ch) >= 2:
            yield i, "".join(ch)
            i = j
        else:
            i += 1


def find_positions(exe, s):
    b = s.encode("utf-16-le")
    out = []
    i = 0
    while True:
        i = exe.find(b, i)
        if i < 0:
            return out
        pre_ok = (i == 0) or exe[i - 2:i] == b"\x00\x00"
        post = exe[i + len(b):i + len(b) + 2]
        post_ok = (len(post) < 2) or (post == b"\x00\x00")
        if pre_ok and post_ok:
            out.append(i)
        i += 1


def ident_neighbours(exe, pos, klen, span=0x80):
    lo = max(0, pos - span)
    hi = min(len(exe), pos + klen * 2 + span)
    n = 0
    for off, txt in iter_utf16(exe, lo, hi):
        if off == pos:
            continue
        if IDENT.match(txt):
            n += 1
    return n


def main():
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
        k = p[0]
        sc = p[2] if len(p) > 2 else ""
        if sc in ("t", "x") or k in draw or k.startswith("%"):
            continue
        rows.append(k)

    risky = []
    for k in rows:
        worst = 0
        for pos in find_positions(exe, k):
            worst = max(worst, ident_neighbours(exe, pos, len(k)))
        if worst >= 2:
            # 自己像标识符的，风险更高
            risky.append((worst, IDENT.match(k) is not None, k))

    risky.sort(key=lambda t: (-t[0], -int(t[1]), t[2]))
    print("会写内存 %d 条，疑似程序标识符 %d 条" % (len(rows), len(risky)))
    print()
    for n, is_ident, k in risky:
        print("  %2d 邻居  %-5s %r" % (n, "标识符" if is_ident else "短语", k))

    with open(os.path.join(ROOT, "_risky_ident.txt"), "w",
              encoding="utf-8") as f:
        for n, is_ident, k in risky:
            f.write("%d\t%d\t%s\n" % (n, int(is_ident), k))
    print()
    print("已写 _risky_ident.txt")


if __name__ == "__main__":
    main()
