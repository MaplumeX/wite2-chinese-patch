#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四批：部队详情 / 补给面板里的列头缩写

发现的规律
----------
这些缩写和它们的全称在 `.rdata` 里**严格交错**存放，一个缩写跟一个全称：

    0x9335D8 'AmmoR'              0x9335E4 'Ammo Received'
    0x933644 'Veh%'               0x9335B0 'Vehicles Percentage'
    0x93369C 'VehN'               0x93360C 'Vehicles Need'
    0x9336D0 'SupCons'            0x9336E0 'Supply Consumed'
    0x933720 'HQPen'              0x93372C 'HQ Admin Roll Penalty'

所以后缀可以直接读出来：

    R = Received      接收        N = Need        需求
    % = Percentage    百分比      Cons = Consumed 消耗

之前漏掉是因为：这些串是 2-7 个字符的混合大小写，既没有空格也不是全大写，
`inventory.py` 的排除规则和 `short_class()` 的长度判断都没覆盖到它们。

不确定的一律不加
----------------
下面 `UNSURE` 里的串含义无法从邻近全称确定，宁可按原样留英文，
等进游戏确认。翻错比不翻更糟 —— 例：`Major` 在地图编辑器里是
"主要河流"，按军衔翻会变成「少校河流」。

用法:
    python tools/add_unit_labels4.py --dry-run
    python tools/add_unit_labels4.py
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")

# 部队详情面板（0x9330A4-0x9333C0）
UNIT_DETAIL = {
    "DtHQ": "距司令部",          # Distance to HQ
    "%Toe": "编制%",            # TOE percentage
    "Prep": "战斗准备",          # Combat Prep
    "ToeM": "最大编制",          # Max TOE
    "SPri": "补给优先级",        # Supply Priority
    "WDest": "撤离目的地",       # Withdraw Destination
    "WTurn": "撤离回合",         # Withdraw Turn
    "TtOB": "编制变更回合",      # Turns to OB change
    "WStat": "撤离状态",         # Withdraw Status
    "Won": "胜",                # Battles Won
    "ELt": "精锐",              # Elite status
}

# 补给统计面板（0x933460-0x9337A0），后缀有规律
SUPPLY_STATS = {
    "Sup%": "补给百分比",        # Supply Percentage
    "SupN": "补给需求",          # Supply Need
    "SupCons": "补给消耗",       # Supply Consumed
    "FuelN": "燃料需求",         # Fuel Need
    "AmmoR": "弹药接收",         # Ammo Received
    "AmmoN": "弹药需求",         # Ammo Need
    "Veh%": "车辆百分比",        # Vehicles Percentage
    "VehR": "车辆接收",          # Vehicles Received
    "VehN": "车辆需求",          # Vehicles Need
    "MenR": "人员接收",          # Men Received
    "HQPen": "司令部惩罚",       # HQ Admin Roll Penalty
    "AdminF": "行政检定失败",     # Admin Rolls Failed
}

# 空中单位面板（0x92F4C4-0x92F550）
AIR_PANEL = {
    "CP": "指挥点",             # Command Points
    "CPL": "指挥点上限",         # Command Point Limit
    "Nav": "海军",              # Naval Status
}

GROUPS = [
    ("部队详情列头", UNIT_DETAIL),
    ("补给统计列头", SUPPLY_STATS),
    ("空中单位列头", AIR_PANEL),
]

# 含义不确定，故意不翻
UNSURE = {
    "APri": "0x92F534 空中单位面板，邻居 Follow HQ/Airbase/Mission Settings；"
            "可能是 Assets Priority 或 Airbase Priority，两者译法不同",
    "Naval": "0x92ECBC 既是『海军状态』缩写也是国家名，需分上下文",
    "Major": "0x9A28C4 地图编辑器『主要河流』，非军衔",
    "Private": "0x9DF3B4 PBEM JSON 字段名，非军衔",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)
    draw_only = set(dic.get("_drawOnly", []))
    low = {k.lower() for k in dic if not k.startswith("_")}

    ok, skip, conflict = [], [], []
    for title, table in GROUPS:
        print("\n=== %s ===" % title)
        for src, dst in table.items():
            have = dic.get(src)
            if have is None and src.lower() in low:
                skip.append(src)
                print("  -- %-10r 已有忽略大小写同形" % src)
                continue
            if have is not None:
                if have == dst:
                    skip.append(src)
                    print("  -- %-10r 已一致 %r" % (src, have))
                elif args.force:
                    ok.append((src, dst))
                    print("  !! %-10r %r -> %r (force)" % (src, have, dst))
                else:
                    conflict.append((src, have, dst))
                    print("  !! %-10r 现有=%r 拟改=%r（保留）" % (src, have, dst))
                continue
            ok.append((src, dst))
            print("   + %-10r %-12r t(显式)" % (src, dst))

    print("\n合计: 可加 %d / 跳过 %d / 冲突 %d" % (len(ok), len(skip), len(conflict)))
    print("\n有意不翻（%d 条）：" % len(UNSURE))
    for k, why in UNSURE.items():
        print("   %-10r %s" % (k, why))

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return 0
    if not ok:
        return 0

    for src, dst in ok:
        dic[src] = dst
        draw_only.add(src)        # 一律绘制层：多副本，不改内存
    dic["_drawOnly"] = sorted(draw_only)
    dic["_ambiguous"] = {**dic.get("_ambiguous", {}), **UNSURE}

    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入 %d 条，全部进 _drawOnly（共 %d）" % (len(ok), len(draw_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
