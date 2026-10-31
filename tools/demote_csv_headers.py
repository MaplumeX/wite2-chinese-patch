"""把 CSV 表头里有译文的列名降级为「只在绘制层翻译」。

为什么
------
游戏用列名字符串去匹配 CSV 表头来定位字段。把 .rdata 里的列名改成
中文，匹配就失败，读出来是 NULL，用到的地方直接崩。

真实事故：Motorized 改成"摩托化"后，点"指挥官报告"必崩。它在
Units.csv 的表头里（第 20 列），也在 exe 里和一串别的列名紧挨着：
Motorized, Cur, Delay, Front, ArriveY, ArriveX。

降级到绘制层就两全：表格列头照常显示中文，内存里的原文不动，
游戏解析 CSV 不受影响。
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


def csv_headers():
    """所有数据文件的表头字段名。"""
    out = set()
    pattern = os.path.join(GAME, "Dat", "CSV", "**", "*.csv")
    for path in glob.glob(pattern, recursive=True):
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            continue
        first = text.split("\n", 1)[0]
        for name in next(csv.reader(io.StringIO(first)), []):
            name = name.strip().lstrip("\ufeff")
            if name:
                out.add(name)
    return out


def main():
    headers = csv_headers()
    print("数据文件表头字段 %d 个" % len(headers))

    raw = json.load(open(DICT, encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))
    words = {k for k in raw if not k.startswith("_")}

    # 已经在表头里的词典条目
    hit = sorted(headers & words)
    print("其中在词典里有译文的 %d 条" % len(hit))
    print()

    # 还要查：表头字段被词典条目当作前缀/后缀拼的情况（Upgrade / Upgrade to:）
    changed = 0
    for w in hit:
        if w in draw:
            print("  %-22r 已经是绘制层" % w)
            continue
        raw.setdefault("_drawOnly", []).append(w)
        draw.add(w)
        changed += 1
        print("  %-22r -> %-16r 降级为绘制层" % (w, raw[w]))

    # 显式列出的 JSON 字段名（不在 CSV 表头里，但同样是程序查找键）
    EXTRA = ["Message"]
    for w in EXTRA:
        if w in words and w not in draw:
            raw.setdefault("_drawOnly", []).append(w)
            draw.add(w)
            changed += 1
            print("  %-22r -> %-16r 降级为绘制层（JSON 字段名）"
                  % (w, raw[w]))

    if changed:
        with open(DICT, "w", encoding="utf-8", newline="\n") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
    print()
    print("降级 %d 条" % changed)


if __name__ == "__main__":
    main()
