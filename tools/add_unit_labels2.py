#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二批：编制类型表 + 将领属性 + 飞行队统计列头

第一轮（tools/add_unit_labels.py）只补了用户直接点名的几条。这里把
**同一张表剩下的**整批补齐，避免 "Artillery 翻了但 Motorized 没翻"
这种半吊子状态。

发现的表结构（.rdata 连续布局，UTF-16）：

    0x9328FC-0x93297C   编制规模全称   Brigade/Division/Front/Company/…
    0x93297C-0x932AA0   兵种缩写       Mech/Cav/Airb/Mot/Crps/SPGun/…
    0x932AAC-0x932C98   兵种全称       Infantry/Motorized/Mechanized/…
    0x932D88-0x932FD0   编制操作       Refit/Set MAX TOE percent/…
    0x932144-0x932178   将领属性       Def/Defeats/Vic/Victories/HQ Types
    0x92D0C8-0x92D154   飞行队任务统计  SKILL/DIS/DET/LOSS/KILL/DAMD/…

全部走绘制层（TSV 第三列 t）：这些短串在 .rdata 里有多份副本
（'Artillery' 实测 6 处 UTF-16 + 7 处 ASCII），原地改写会连枚举
副本一起改，静默破坏。绘制层只换这一次绘制用的指针，不动内存。

用法:
    python tools/add_unit_labels2.py --dry-run
    python tools/add_unit_labels2.py
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")

# ---- 编制规模 ----
ECHELON = {
    "Brigade": "旅",
    "Division": "师",
    "Front": "方面军",
    "Company": "连",
    "Battalion": "营",
    "Regiment": "团",
}

# ---- 兵种缩写（列头用，尽量短）----
ARM_ABBR = {
    "Mech": "机械",
    "Airb": "空降",
    "Mot": "摩托",
    "Army": "集团军",
    "Crps": "军",
    "SPGun": "自行炮",
    "MtInf": "山地兵",
    "Rckt": "火箭",
    "Mort": "迫击",
    "Const": "建筑",
    "AirBase": "空军基地",
    "Fort": "要塞",
    "AirLnd": "空降着陆",
    "Prtsn": "游击队",
}

# ---- 兵种全称 ----
ARM_FULL = {
    "Infantry": "步兵",
    "Motorized": "摩托化",
    "Mechanized": "机械化",
    "Armored": "装甲",
    "Anti-Tank": "反坦克",
    "Cavalry": "骑兵",
    "Construction": "建筑",
    "Engineer": "工兵",
    "Self-Propelled Gun": "自行火炮",
    "Mountain Infantry": "山地步兵",
    "Security": "治安",
    "Rocket": "火箭",
    "Mortar": "迫击炮",
    "Air Landing": "空降着陆",
    "Partisan": "游击队",
    "Machine Gun": "机枪",
}

# ---- 司令部 / 编制操作 ----
HQU = {
    "Corps HQ": "军司令部",
    "Front HQ": "方面军司令部",
    "Units with ": "部队含有 ",
    "rdy ": "就绪 ",
    "dam ": "损伤 ",
    "Remove": "移除",
    "REM": "移除",
    "Refit": "补充休整",
    "Set MAX TOE percent": "设置最大编制百分比",
    "Ground Type": "地面类型",
    "Gnd Type": "地面类型",
    "HQ Chain": "司令部链",
    "HQC": "司令部链",
    "Unit OB": "部队编制",
    "OB": "编制",
}

# ---- 将领属性 tooltip ----
LEADER = {
    "Def": "防御",
    "Defeats": "败绩",
    "Vic": "胜绩",
    "Victories": "胜绩",
    "HQ Types": "司令部类型",
}

# ---- 飞行队任务统计（只挑含义明确的）----
AIRSTAT = {
    "SKILL": "技能",
    "LOSS": "损失",
    "KILL": "击杀",
    "DAMD": "击伤",
}

ALL = {}
for g in (ECHELON, ARM_ABBR, ARM_FULL, HQU, LEADER, AIRSTAT):
    ALL.update(g)


def short_class(k):
    """复刻 build_assets.short_class"""
    if " " in k or "\t" in k:
        return 0
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-:'#]*", k):
        return 0
    return len(k) <= 6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dic = json.load(open(DICT, encoding="utf-8"))
    draw_only = set(dic.get("_drawOnly", []))

    ok, skip, conflict = [], [], []
    for src, dst in ALL.items():
        if src in dic:
            if dic[src] != dst:
                conflict.append((src, dic[src], dst))
            else:
                skip.append(src)
            continue
        ok.append((src, dst))

    print("待加 %d 条：可加 %d / 已在词典 %d / 冲突 %d\n"
          % (len(ALL), len(ok), len(skip), len(conflict)))
    for s, old, new in conflict:
        print("  !! %r 现有=%r 拟改=%r（保留现有）" % (s, old, new))
    if conflict:
        print()

    need_explicit = []
    for src, dst in ok:
        if " " in src or len(src) > 6:
            need_explicit.append(src)
            scope = "t(显式)"
        else:
            scope = "t(自动)"
        print("  %-24r %-10r %s" % (src, dst, scope))

    if args.dry_run:
        print("\n(dry-run)")
        return 0

    for src, dst in ok:
        dic[src] = dst
    for k in need_explicit:
        draw_only.add(k)
    dic["_drawOnly"] = sorted(draw_only)

    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入 %d 条，其中 %d 条显式进 _drawOnly（共 %d）"
          % (len(ok), len(need_explicit), len(draw_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
