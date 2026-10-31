#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第三批：.data 段标签 + 指挥官报告缩写 + CV

为什么前两批没覆盖到
--------------------
前两批只扫 `.rdata`（UTF-16 + ASCII）。但游戏有几张表**只存在于 `.data`**，
而且拼写和 `.rdata` 里的不一样：

    .data 0xA5055C  'Motorized Infantry'   (.rdata 只有 'Motorized')
    .data 0xA505D4  'Corp HQ'              (.rdata 是 'Corps HQ'，多了个 s)
    .data 0xA506C4  'Machinegun'           (.rdata 是 'Machine Gun'，带空格)
    .data 0xA506D8  'Air base'             (.rdata 是 'Air Base'，大写 B)

玩家在部队详情里看到的是 `Machinegun`（无空格）—— 只有 `.data` 那份长这样，
所以显示走的是 `.data` 表。`.rdata` 里翻好了也没用。

`.data` 是**可写段**，且这些是枚举索引表（步长 0x14），原地改写会破坏
其他用途的副本。全部走绘制层。

表结构（VA，ASCII，步长 0x14）：

    0xA503A4-0xA50444  第一组   Infantry/Artillery/SP Weapon/…
    0xA50520-0xA50714  第二组   none/Armor/…/Machinegun/Air base/Naval
    0xA50840-0xA508B8  第三组   none/Combat/HQ/Support/Partisan/…

同表里 `Armor`/`Naval`/`Air base`/`Multi-Role`/`City Fort` 词典里已有
（大小写不敏感命中 `armor`/`NAVAL`/`Air Base`/…），只补真正缺的。

歧义串的处理
------------
绘制层是按**整串精确匹配**查词，不是子串替换。所以风险不在"长句里的
某个词被误换"，而在"同一个短串在两个界面含义不同，却只能有一个译文"。

本脚本末尾的 `AMBIGUOUS` 记录了**故意不翻**的串和原因。典型陷阱：

    'Major'   0x9A28C4  地图文字编辑器的「主要河流」，与 'Minor'(次要) 配对
                        —— 不是军衔。翻了就是「少校河流」。
    'Private' 0x9DF3B4  PBEM 存档的 JSON 字段名（邻居 StepMaxTime /
                        ChallengePassword）—— 不是军衔。

这类必须先确认上下文再动，宁可留英文。

用法:
    python tools/add_unit_labels3.py --dry-run
    python tools/add_unit_labels3.py
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")

# ---- .data 独占的兵种标签（.rdata 里拼写不同，所以以前没命中）----
DATA_ONLY = {
    "Machinegun": "机枪",
    "Motorized Infantry": "摩托化步兵",
    "Corp HQ": "军司令部",
}

# ---- 指挥官报告列头缩写（0x92E790-0x932200，缩写紧跟全称）----
REPORT_HEADERS = {
    "ThBox": "战区",          # Theater Box
    "Depl": "损耗",           # Depletion
    "SupL": "支援等级",        # Support Level
    "ComPt": "指挥点",         # Command Points
    "Frzn": "冻结",           # Frozen
    "Restr": "限制",          # Restriction
    "DisC": "解职花费",        # Dismiss cost
    "MaxC": "最大指挥",        # Max command
    "Fate": "命运",           # Leader's fate
    "Ftr*": "战斗机*",         # 带星号的战斗机条目
    "Executed": "已执行",
    "UnitSup": "已补给部队",    # Units Supplied
    "HQ": "司令部",
}

# ---- 设施/生产标签 ----
FACILITY = {
    "Rail": "铁路",
    "VW": "V型武器",          # V-Weapon
    "VW-L": "V型发射场",       # V-Weapon launch site
    "UB": "潜艇",             # U-Boat
    "UBP": "潜艇坞",           # U-Boat pen site
}

# ---- 战斗力 ----
# 与既有 'Combat Value'->战斗力 / 'CV (REQ)'->战斗力（需求）保持一致
CV = {
    "CV": "战斗力",
    "CV %d.%d": "战斗力 %d.%d",
}

GROUPS = [
    (".data 独占标签", DATA_ONLY),
    ("指挥官报告列头", REPORT_HEADERS),
    ("设施标签", FACILITY),
    ("战斗力", CV),
]

# 故意不翻：记录原因，避免以后又加回去
AMBIGUOUS = {
    "Major": "0x9A28C4 是地图文字编辑器的「主要河流」(与 Minor 次要配对)，不是军衔",
    "Private": "0x9DF3B4 是 PBEM 存档 JSON 字段名(邻居 StepMaxTime/ChallengePassword)，不是军衔",
    "%s, %s": "0x931F78 将领姓名拼接符，涉及姓名不译约定",
    " ( ": "0x932D14 纯排版标点",
    "%d.%d": "0x932D58 纯数字格式，无文字",
    "ppp": "占位垃圾串",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="覆盖已有译文（默认保留现有）")
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
                # 大小写不敏感已有等价条目，绘制层能命中
                skip.append(src)
                print("  -- %-24r 已有(忽略大小写同形)" % src)
                continue
            if have is not None:
                if have == dst:
                    skip.append(src)
                    print("  -- %-24r 已存在且一致 %r" % (src, have))
                elif args.force:
                    ok.append((src, dst))
                    print("  !! %-24r %r -> %r (force)" % (src, have, dst))
                else:
                    conflict.append((src, have, dst))
                    print("  !! %-24r 现有=%r 拟改=%r（保留现有）"
                          % (src, have, dst))
                continue
            ok.append((src, dst))
            print("   + %-24r %-12r t(显式)" % (src, dst))

    print("\n合计: 可加 %d / 跳过 %d / 冲突 %d" % (len(ok), len(skip), len(conflict)))

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return 0
    if not ok:
        print("没有需要新增的条目")
        return 0

    for src, dst in ok:
        dic[src] = dst
        draw_only.add(src)          # 一律绘制层：多副本 + 枚举表，不动内存
    dic["_drawOnly"] = sorted(draw_only)
    dic["_ambiguous"] = AMBIGUOUS   # 元数据，build_assets 不读

    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入 %d 条，全部进 _drawOnly（共 %d 条）"
          % (len(ok), len(draw_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
