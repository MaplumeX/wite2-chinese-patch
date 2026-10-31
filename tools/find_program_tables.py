"""找出「程序标识符表」里混进去的词典条目。

原理
----
UTF-16 字符串在 .rdata 里如果是**紧挨着**排的（间距正好等于
len*2+2，中间没有对齐填充），说明它们来自同一个字符串字面量数组，
通常是程序自己在用的表：JSON 字段名、配置键、枚举成员。

区分「程序表」和「显示文本表」的办法：看这块里有多少词在词典里。
- 月份表 January..December：12 个词，词典收录 12 个 → 显示文本
- Steam 登录响应表：Token / FQDN / Result / UserID / ExpirationDate /
  ErrorCode / Message / ErrorComment / DebugMessage / Password /
  Login / authentication/steam/login/anonymous → 词典只命中
  Message / Result / Login 三个，其余都是技术词 → 程序表

程序表里的词即使看着像显示文本也不能改内存。真实事故：Message
改成"消息"后点"通讯报告"必崩，因为游戏拿它去解析登录响应，
拿到 NULL 就崩。
"""
import json
import os
import re

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV = os.path.join(ROOT, "wite2_zh.tsv")
DICT = os.path.join(ROOT, "zh_dict.json")

# 只有完全无空格、无标点的 token 才算这一族的成员
TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,40}$")

MIN_BLOCK = 4        # 至少连续几个才算一块
HIT_RATIO = 0.5      # 命中词典比例低于这个，就是程序表


def scan_runs(exe):
    """扫出所有「紧挨着的 UTF-16 token」连续块。

    紧挨的定义：后一个串的起点 == 前一个起点 + len*2 + 2。
    中间只要有一字节间隔（对齐填充、别的数据），块就断开。
    """
    runs = []
    i = 0
    n = len(exe)
    cur = []
    while i < n - 4:
        j = i
        ch = []
        while j < n - 2 and exe[j + 1] == 0 and 32 <= exe[j] < 127:
            ch.append(chr(exe[j]))
            j += 2
        if len(ch) >= 2 and j < n and exe[j] == 0 and exe[j + 1] == 0:
            txt = "".join(ch)
            if TOKEN.match(txt):
                if cur and cur[-1][0] + len(cur[-1][1]) * 2 + 2 == i:
                    cur.append((i, txt))
                else:
                    if len(cur) >= MIN_BLOCK:
                        runs.append(cur)
                    cur = [(i, txt)]
                i = j + 2
                continue
            else:
                if len(cur) >= MIN_BLOCK:
                    runs.append(cur)
                cur = []
        else:
            if len(cur) >= MIN_BLOCK:
                runs.append(cur)
            cur = []
        i += 1
    if len(cur) >= MIN_BLOCK:
        runs.append(cur)
    return runs


def main():
    exe = open(EXE, "rb").read()
    raw = json.load(open(DICT, encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))
    words = {k for k in raw if not k.startswith("_")}

    mem = {}
    for line in open(TSV, encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 2:
            continue
        sc = p[2] if len(p) > 2 else ""
        mem[p[0]] = sc

    runs = scan_runs(exe)
    print("紧挨的标识符块：%d 个" % len(runs))

    offences = []
    for run in runs:
        words_in = [t for _, t in run]
        hits = [w for w in words_in if w in words]
        if not hits:
            continue
        ratio = len(hits) / len(words_in)
        if ratio >= HIT_RATIO:
            continue        # 显示文本表，正常
        for w in hits:
            sc = mem.get(w)
            if sc in ("t", "x"):
                continue    # 已经不写内存
            offences.append((len(words_in), ratio, w, words_in[:8]))

    offences.sort(key=lambda t: (-t[0], t[2]))
    print()
    print("要降级为「只在绘制层」的条目：%d 条" % len(offences))
    for total, ratio, w, sample in offences:
        print("  块%-3d 命中%.0f%%  %-22r  邻居: %s"
              % (total, ratio * 100, w,
                 ", ".join(sample[:6])))

    with open(os.path.join(ROOT, "_program_tables.txt"), "w",
              encoding="utf-8") as f:
        for total, ratio, w, sample in offences:
            f.write("%s\t%d\t%.2f\t%s\n"
                    % (w, total, ratio, "|".join(sample)))
    print()
    print("已写 _program_tables.txt")


if __name__ == "__main__":
    main()
