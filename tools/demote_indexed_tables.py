"""把「游戏按名字查表」的主数据表 Name 列取值，降到绘制层。

判据
----
Dat/CSV/Generic 下的主数据表，游戏启动时读入、按 Name 建索引，之后
拿名字回查记录。改了名字就查不到，返回 NULL。调用方通常不判空，
直接拿它当字符串去格式化 —— 于是崩在 printf 之类的代码里，看起来
完全不像"字符串改坏了"。

崩溃现场（指挥官报告）：
  addr=009CF80B  落在 .rdata 里 tooltip 片段 ' Damaged %d' 中间
  access=WRITE target=002C006E  往 0x2C006E 写
  eax=002C0032 / target=002C006E  都是字符串内容被当成指针
  esp=004FE678 ebp=004FE6E0      都落在 .text 段，栈已崩坏

也就是：某处拿到一个本该是指向记录的指针，实际拿到了一串文本，
按指针去写就崩了。指挥官报告按兵种类型（GroundTypes）汇总部队，
GroundTypes.Name 又被改成了中文 —— 查不到记录，返回的就是野指针。

为什么不降 locations
--------------------
Locations.csv 有 4401 条地名，降级会让地图标签和列表的排版全部
错位（只绘制层 = 游戏按英文宽度测量）。先处理主数据表，如果还崩
再动地名。
"""
import csv
import io
import json
import os

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT = os.path.join(ROOT, "zh_dict.json")

# 游戏启动时加载、按 Name 建索引的主数据表
INDEXED_TABLES = [
    "GroundTypes", "Nations", "AirProfiles", "Aircraft",
    "Devices", "Leaders", "Ground", "OBs",
]


# 索引键所在的列。OBs 的主键是 ID，Suffix 只是编制描述（显示用），
# 游戏不会拿 Suffix 去查表，降级它只会让部队列表大面积错位。
NAME_COLUMNS = {
    "OBs": ("Name",),
}
DEFAULT_COLUMNS = ("Name", "Suffix")


def load_names(table):
    path = os.path.join(GAME, "Dat", "CSV", "Generic", table + ".csv")
    if not os.path.exists(path):
        return []
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    out = []
    cols = NAME_COLUMNS.get(table, DEFAULT_COLUMNS)
    for row in csv.DictReader(io.StringIO(text)):
        for col in cols:
            v = (row.get(col) or "").strip()
            if v:
                out.append(v)
    return out


def main():
    raw = json.load(open(DICT, encoding="utf-8"))
    draw = set(raw.get("_drawOnly", []))

    changed = 0
    for table in INDEXED_TABLES:
        names = load_names(table)
        hit = [v for v in names
               if v in raw and not v.startswith("_") and v not in draw]
        if not hit:
            continue
        for v in hit:
            raw["_drawOnly"].append(v)
            draw.add(v)
        changed += len(hit)
        print("%-12s 降级 %3d 条  (例: %s)"
              % (table, len(hit), ", ".join(repr(x) for x in hit[:3])))

    with open(DICT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)

    print()
    print("共降级 %d 条，_drawOnly 现有 %d 条"
          % (changed, len(raw["_drawOnly"])))


if __name__ == "__main__":
    main()
