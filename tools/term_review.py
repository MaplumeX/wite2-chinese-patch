#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
术语审查表生成器

把词典里成体系的术语抽出来, 按类别整理成一张表供人工审校。

用法:
    python tools/term_review.py export   生成 term_review.csv
    python tools/term_review.py import   把 term_review.csv 的修改导回 zh_dict.json
    python tools/term_review.py check    检查术语一致性(同一英文词根的不同译法)
"""

import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
OUT = os.path.join(ROOT, "term_review.csv")

FIELDS = ["类别", "英文", "当前译文", "建议修改", "备注"]


def load():
    with open(DICT, encoding="utf-8") as f:
        return json.load(f)


def classify(en, zh):
    """把词条分到术语类别, 返回 (类别, 备注)"""
    e = en

    # 编制单位
    if re.search(r"\b(Army Group|Panzer Group|Panzer Army|Army|Corps|Korps|"
                 r"Division|Brigade|Regiment|Battalion|Company|Squadron|Squad|"
                 r"Front|Detachment|Command)\b", e):
        note = ""
        if "Front" in e:
            note = "苏军编制, 对应德军集团军群"
        elif "Army Group" in e:
            note = "德军编制"
        elif "Panzer" in e:
            note = "德军装甲部队"
        return "编制单位", note

    # 兵种
    if re.search(r"\b(Infantry|Cavalry|Motorized|Mechanized|Panzer|Armored|"
                 r"Rifle|Guards|Security|Light|Airborne|Mountain|Tank)\b", e):
        return "兵种", ""

    # 游戏选项
    if re.search(r"(Level|Control|Attrition|Motorization|Invasions|"
                 r"Difficulty|Deaths|Fog of War|Operations|Fog)", e):
        return "游戏选项", ""

    # 补给后勤
    if re.search(r"\b(Supply|Fuel|Ammo|Freight|Depot|Logistics|Transport|"
                 r"Rail|Port|Capacity|Stockpile|Need)\b", e):
        return "补给后勤", ""

    # 空军
    if re.search(r"\b(Air|Aircraft|Plane|Pilot|Sortie|Flak|Recon|Bomb|"
                 r"Escort|Intercept|Patrol|Sweep|Airfield|Airbase|AOG)\b", e):
        return "空军", ""

    # 战斗
    if re.search(r"\b(Combat|Attack|Defense|Assault|Retreat|Rout|Loss|"
                 r"Casualt|Odds|CV|MP)\b", e):
        return "战斗", ""

    # 生产
    if re.search(r"\b(Factory|Production|Manpower|Resource|Oil|Industry|"
                 r"Armament|Vehicle|Airframe|Chassis|Railyard|Hiwi)\b", e):
        return "生产", ""

    # 部队状态
    if e in ("READY", "RDY", "RESERVE", "REFIT", "STATIC", "DEPLETED",
             "ISOLATED", "ROUTED", "GARRISON", "LOADED", "WITHDRAW",
             "MOTORIZED", "RESTRICTED", "LOCKED", "Unready", "Rest",
             "Training", "Priority", "Assigned", "Disband"):
        return "部队状态", ""

    return None, None


def cmd_export():
    d = load()
    rows = []
    for en, zh in d.items():
        if en.startswith("_") or zh.startswith("----"):
            continue
        cat, note = classify(en, zh)
        if not cat:
            continue
        rows.append({"类别": cat, "英文": en, "当前译文": zh,
                     "建议修改": "", "备注": note})

    order = {"编制单位": 0, "兵种": 1, "部队状态": 2, "战斗": 3,
             "补给后勤": 4, "空军": 5, "生产": 6, "游戏选项": 7}
    rows.sort(key=lambda r: (order.get(r["类别"], 99), r["英文"]))

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    cnt = {}
    for r in rows:
        cnt[r["类别"]] = cnt.get(r["类别"], 0) + 1
    print("导出术语 %d 条 -> %s" % (len(rows), OUT))
    for k in sorted(cnt, key=lambda x: order.get(x, 99)):
        print("   %-8s %d" % (k, cnt[k]))
    return 0


def cmd_import():
    if not os.path.exists(OUT):
        print("找不到 %s, 先跑 export" % OUT)
        return 1
    with open(OUT, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    d = load()
    changed = 0
    for r in rows:
        en = (r.get("英文") or "").strip()
        new = (r.get("建议修改") or "").strip()
        if not en or not new:
            continue
        if en not in d:
            print("  警告: 词典里没有 %r" % en)
            continue
        if d[en] != new:
            print("  %-40s %s -> %s" % (en, d[en], new))
            d[en] = new
            changed += 1

    if changed:
        with open(DICT, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print("\n已更新 %d 条术语" % changed)
    else:
        print("没有需要更新的条目")
    return 0


def cmd_check():
    """检查同一英文词根是否有不一致的译法"""
    d = load()
    roots = {
        "Army Group": [], "Panzer": [], "Corps": [], "Division": [],
        "Brigade": [], "Regiment": [], "Battalion": [], "Front": [],
        "Supply": [], "Fuel": [], "Ammo": [], "Depot": [],
        "Air": [], "Attack": [], "Defense": [], "Retreat": [],
        "Factory": [], "Level": [], "Control": [], "Port": [],
    }
    for en, zh in d.items():
        if en.startswith("_") or zh.startswith("----"):
            continue
        for r in roots:
            if r in en:
                roots[r].append((en, zh))

    print("=== 词根译法一致性检查 ===\n")
    issues = 0
    for r, items in roots.items():
        if len(items) < 2:
            continue
        # 看译文里有没有共同的对应词
        print("[%s] %d 条" % (r, len(items)))
        for en, zh in items[:8]:
            print("    %-42s %s" % (en, zh))
        if len(items) > 8:
            print("    ... 还有 %d 条" % (len(items) - 8))
        print()
    return 0


def main():
    cmds = {"export": cmd_export, "import": cmd_import, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        return 1
    return cmds[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
