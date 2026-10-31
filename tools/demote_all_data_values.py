"""把「还会被写进内存」的条目，和 Dat/ 下所有数据文件的所有取值对撞。

为什么是全集
------------
之前只查了 Name / Suffix / ID / Type / Class / Nation 这几列。但游戏
也会拿别的列当键（SquadDis 描述、MapText、角色名……）。漏一列就漏
一批键，改了就崩。

原则：只要一个串在数据文件里出现过，游戏就有可能拿它做查找/比较，
就不该改内存。剩下的只有 .rdata 里那些纯界面标签。
"""
import csv
import glob
import io
import json
import os

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT = os.path.join(ROOT, "zh_dict.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

MIN_LEN = 2


def all_cell_values():
    """Dat/ 下所有 CSV 的所有单元格取值。"""
    out = set()
    for path in glob.glob(os.path.join(GAME, "Dat", "**", "*"),
                          recursive=True):
        if not os.path.isfile(path):
            continue
        low = path.lower()
        if not (low.endswith(".csv") or low.endswith(".txt")):
            continue
        raw = open(path, "rb").read()
        if b"\x00" in raw[:4096]:
            continue        # 二进制
        for enc in ("utf-8-sig", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            continue
        if low.endswith(".csv"):
            for row in csv.reader(io.StringIO(text)):
                for v in row:
                    v = v.strip()
                    if MIN_LEN <= len(v) <= 120 and v.isascii():
                        out.add(v)
        else:
            for line in text.splitlines():
                line = line.strip()
                if MIN_LEN <= len(line) <= 120 and line.isascii():
                    out.add(line)
    return out


def main():
    vals = all_cell_values()
    print("Dat/ 下的不同取直：%d 个" % len(vals))

    raw = json.load(open(DICT, encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))

    mem = []
    for line in open(TSV, encoding="utf-8"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 2:
            continue
        sc = p[2] if len(p) > 2 else ""
        if sc in ("t", "x"):
            continue
        mem.append(p[0])

    hit = sorted(set(mem) & vals)
    print("会写内存 %d 条，其中出现在数据文件里 %d 条" % (len(mem), len(hit)))
    print()

    for w in hit:
        print("  %r -> %r" % (w, raw[w]))

    if not hit:
        return

    for w in hit:
        if w in draw:
            continue
        raw.setdefault("_drawOnly", []).append(w)

    with open(DICT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    print()
    print("降级 %d 条，_drawOnly 现有 %d 条"
          % (len(hit), len(raw["_drawOnly"])))


if __name__ == "__main__":
    main()
