"""把数据文件 Name 列里的取值降级为「只在绘制层翻译」。

为什么
------
这些串是**数据层标识符**，不是界面文本。游戏按名字在这些表里查记录
（兵种类型 → GroundTypes.csv、国家 → Nations.csv、机型 → AirProfiles.csv），
改了名字就查不到，拿到 NULL 再解引用就是崩溃。

真实事故：GroundTypes.csv 的 "Rifle Squad" 改成"步兵班"后，
点"指挥官报告"必崩 —— 那个界面按兵种类型查表。

降级到绘制层两全：列表里仍显示中文，内存里的原文不动。

和 tools/demote_csv_headers.py 的区别
--------------------------------------
那个处理**列名**（表头，游戏按列名定位字段）；
这个处理**取值**（表体，游戏按取值查找记录）。
两者都会崩，原因不同。
"""
import csv
import glob
import io
import json
import os

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT = os.path.join(ROOT, "zh_dict.json")

# 只有这些列装的是「按名字查找的记录标识符」。
# 其余列是数字、日期、标记，不参与按名查找。
NAME_COLUMNS = {"Name", "Suffix", "ID", "Type", "Class", "Nation"}


def csv_rows(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    return []


def main():
    values = {}          # 取值 -> 出现它的文件名集合
    pattern = os.path.join(GAME, "Dat", "CSV", "**", "*.csv")
    for path in glob.glob(pattern, recursive=True):
        base = os.path.basename(path)
        for row in csv_rows(path):
            for col in NAME_COLUMNS:
                if col not in row:
                    continue
                v = (row.get(col) or "").strip()
                if not v or not v.isascii():
                    continue
                if v.replace(".", "").replace("-", "").isdigit():
                    continue
                values.setdefault(v, set()).add(base)

    print("数据文件 Name/Suffix 列里的不同取值：%d 个" % len(values))

    raw = json.load(open(DICT, encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))
    words = {k for k in raw if not k.startswith("_")}

    hit = sorted(values.keys() & words)
    print("其中在词典里有译文的：%d 条" % len(hit))
    print()

    changed = 0
    for w in hit:
        mark = "已是绘制层" if w in draw else "降级为绘制层"
        if w not in draw:
            raw.setdefault("_drawOnly", []).append(w)
            draw.add(w)
            changed += 1
        files = sorted(values[w])
        print("  %-26r -> %-18r %s   [%s]"
              % (w, raw[w], mark, ", ".join(files[:2])))

    if changed:
        with open(DICT, "w", encoding="utf-8", newline="\n") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
    print()
    print("降级 %d 条，_drawOnly 现有 %d 条" % (changed, len(draw)))


if __name__ == "__main__":
    main()
