#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补部队详情 / 指挥官报告里的短标签（用户报告的漏翻）

用户看到的现象
--------------
* 部队详情里支援单位类型那列：`Artillery` 是英文，但防空那条
  （`Anti-Aircraft`）是中文 —— 同一张表，翻了一半
* 指挥官报告列头：`AC` `Mrl` 是英文，但 `Experience` `Fatigue`
  （tooltip 里的）是中文

根因
----
`tools/inventory.py` 的排除规则把它们当"配置键名 / 全大写代号"挡掉了：

    BLOCK_EXACT 里明写着 "Art" "Bmr" "Ftr" "KILL" "LOSS" "DAMD" …
    BLOCK_PATTERNS 里有 r"^[A-Z]{2,}\\d*$" → 全大写代号

但那些是 **.data 里的配置键名**，`.rdata` 里的同名串是**显示标签**，
是两回事。于是这批标签从来没进过待翻清单。

为什么全部走绘制层
------------------
实测字节出现次数（要求前后 NUL）：

    'Artillery'   .rdata 6 处 UTF-16 + 7 处 ASCII，.data 2 处 ASCII
    'AA'          .rdata 1 处
    'Mrl'         .rdata 4 处
    'AC'          .rdata 3 处 + 1 处 ASCII

`patch_section` 是**按段全扫精确匹配**，会改掉该段里所有副本。多副本
说明可能有枚举/查找用途，静默破坏。所以这批一律走绘制层（TSV 第三列
`t`）：只在那一次绘制调用里换指针，**不动任何内存字节**。

`short_class()` 只对 ≤6 字符的自动标 t，所以 `Artillery`(9)、
`Air Base`(8)、`SP Artillery`(12)、`Army Group HQ`(13) 这类必须
显式加进 `_drawOnly`，否则会被原地改写。

用法:
    python tools/add_unit_labels.py --dry-run
    python tools/add_unit_labels.py
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")

# 部队详情「支援单位类型」表（.rdata 0x9329A8..0x932B78 一串）
SUPPORT_TYPES = {
    "Artillery": "炮兵",
    "Art": "炮兵",
    "AA": "防空",
    "AT": "反坦克",
    "Cav": "骑兵",
    "Eng": "工兵",
    "MG": "机枪",
    "Sec": "治安",
    "SP Artillery": "自行火炮",
}

# 指挥官报告 / 飞行队列表的列头（.rdata 0x92E790..0x9326D8）
COLUMNS = {
    "Bmr": "轰炸机",
    "Ftr": "战斗机",
    "Mrl": "士气",
    "AC": "飞机",
    "Inf": "步兵",
    "Sup": "补给",
    "Man": "人力",
    "Veh": "车辆",
    "Pop": "人口",
    "Pri": "优先级",
    "Dmg": "损伤",
    "Pil": "飞行员",
    "Upg": "升级",
    "Rpl": "补充",
    "Mis": "任务",
    "AD": "空中指令",
    "HHQ": "上级司令部",
    "HQs": "司令部",
    "KIA": "阵亡",
    "Rel": "可靠",
    "Eff": "效能",
    "Pen": "穿甲",
    "EXC": "优秀",
    "Rng": "射程",
}

# 单位状态 / 空中任务
STATUS = {
    "HELD ": "据守 ",
    "SHATTERED ": "溃散 ",
    "RETREATED ": "撤退 ",
    "CAP": "战斗巡逻",
    "A2A": "空对空",
    "GND": "地面",
    "OPS": "作战",
}

# 完整词组（不进短串自动分类，必须显式 _drawOnly）
PHRASES = {
    "Air Base": "空军基地",
    "Army HQ": "集团军司令部",
    "Army Group HQ": "集团军群司令部",
    "Air Directives": "空中指令",
    "Air Op Group": "航空作战群",
    "Accept Challenge": "接受挑战",
}

ALL = {}
for group in (SUPPORT_TYPES, COLUMNS, STATUS, PHRASES):
    ALL.update(group)

# short_class() 只对 <=6 字符的自动标 t；超长的必须显式进 _drawOnly
NEED_EXPLICIT_DRAWONLY = [k for k in ALL
                          if (" " in k) or len(k) > 6]


def short_class(k):
    """复刻 build_assets.short_class，用来预判每条会不会自动走绘制层"""
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
            existing = dic[src]
            if existing != dst:
                conflict.append((src, existing, dst))
            else:
                skip.append(src)
            continue
        ok.append((src, dst))

    print("待加 %d 条：可加 %d / 已在词典 %d / 与现有译文冲突 %d\n"
          % (len(ALL), len(ok), len(skip), len(conflict)))
    if conflict:
        print("!! 冲突（保留现有译文，请人工确认）：")
        for s, old, new in conflict:
            print("   %r  现有=%r  拟改=%r" % (s, old, new))
        print()

    print("%-16s %-10s %-8s %s" % ("原文", "译文", "作用域", "说明"))
    print("-" * 62)
    for src, dst in ok:
        if src in NEED_EXPLICIT_DRAWONLY:
            scope = "t(显式)"
            note = "含空格或 >6 字符，须加 _drawOnly"
        elif short_class(src):
            scope = "t(自动)"
            note = "short_class 自动判为绘制层"
        else:
            scope = "??"
            note = "未覆盖，需检查"
        print("%-16r %-10r %-8s %s" % (src, dst, scope, note))

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return 0

    for src, dst in ok:
        dic[src] = dst
    # 长词组显式进 _drawOnly，保证不走原地改写
    for k in NEED_EXPLICIT_DRAWONLY:
        if k not in draw_only:
            draw_only.add(k)
    dic["_drawOnly"] = sorted(draw_only)

    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入：%d 条译文，_drawOnly %d -> %d"
          % (len(ok), len(draw_only) - len([k for k in NEED_EXPLICIT_DRAWONLY
                                            if k not in draw_only]), len(draw_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
