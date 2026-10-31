import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT = os.path.join(ROOT, "zh_dict.json")

TARGETS = [
    "HEX TILE ART",
    "Resource production",
    "Synthetic Fuel production",
    "Vehicle production",
    "spanish-puerto rico",
    "spanish-uruguay",
    "spanish-venezuela",
    "swedish-finland",
]

raw = json.load(open(DICT, encoding="utf-8"))
draw = raw.setdefault("_drawOnly", [])
have = set(draw)
n = 0
for k in TARGETS:
    if k in raw and not k.startswith("_") and k not in have:
        draw.append(k)
        have.add(k)
        n += 1
        print("  %-28r -> %-16r 降级" % (k, raw[k]))
    else:
        print("  %-28r 跳过（不存在或已降级）" % k)
json.dump(raw, open(DICT, "w", encoding="utf-8", newline="\n"),
          ensure_ascii=False, indent=1)
print()
print("降级 %d 条，_drawOnly 现有 %d 条" % (n, len(draw)))
