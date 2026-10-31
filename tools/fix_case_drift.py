#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修正大小写变体之间的译文漂移

背景：绘制层是按**整串**查词。游戏在网格/表头里用全大写（`PRIORITY`），
在普通标签里用首字母大写（`Priority`），两者被当成两个独立键；补词时
分批加入，就漂成了两个不同译法。玩家看到的是"同一个词两种翻法"。

实测 15 组大小写变体译文不一致，分三类：

A. 真的该不同（保留）
     RANGE 航程 / Range 射程     飞机航程 vs 火炮射程
     MP 移动力 / mP 极地海洋性   移动点 vs 气候代码
     RD. 步兵师 / Rd. 道路       番号 vs 道路
     MEDIUM 中 / Medium 中型     区间档位 vs 尺寸等级
     ACTIVE 激活 / Active 活动   按钮态 vs 事件条件

B. 刻意用短译（保留，网格宽度受限）
     BLUE 蓝 / Blue 蓝色   RED 红 / Red 红色   white 白 / White 白色

C. 纯粹漂移（本脚本修）
     Airbase  航空基地  vs AirBase/Air Base 空军基地
     Priority 优先      vs PRIORITY/Pri     优先级
     NORMAL   普通      vs Normal           正常
     AIR LOSSES 空战损失 vs Air Losses       空中损失
     flak     高炮      vs Flak             高射炮

用法:
    python tools/fix_case_drift.py --dry-run
    python tools/fix_case_drift.py
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")

# key -> (期望译文, 参照键)
FIXES = {
    "Airbase": ("空军基地", "AirBase / Air Base"),
    "Priority": ("优先级", "PRIORITY / Pri"),
    "NORMAL": ("正常", "Normal"),
    "AIR LOSSES": ("空中损失", "Air Losses"),
    "flak": ("高射炮", "Flak"),
    # 同义词漂移："Ground" 已经占用了「地面」（地形），这里用「在地面」区分
    "on the ground": ("在地面", "On The Ground"),
    "POOL": ("储备池", "Pool"),
}

# 记录在案的「有意保留差异」，防止以后被误统一
KEEP = {
    "RANGE / Range": "航程（飞机） vs 射程（火炮），语义确实不同",
    "MP / mP": "移动力 vs 极地海洋性气候代码，同形不同义",
    "RD. / Rd.": "步兵师番号 vs 道路",
    "MEDIUM / Medium": "区间档位「中」 vs 尺寸等级「中型」",
    "ACTIVE / Active": "按钮激活态 vs 事件条件「活动」",
    "BLUE / Blue, RED / Red, white / White": "短译供窄网格使用，宽度受限",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)

    changed = 0
    for key, (want, ref) in FIXES.items():
        have = dic.get(key)
        if have is None:
            print("  ?? %-12r 词典里没有这个键，跳过" % key)
            continue
        if have == want:
            print("  -- %-12r 已是 %r" % (key, want))
            continue
        print("  -> %-12r %r => %r   (对齐 %s)" % (key, have, want, ref))
        dic[key] = want
        changed += 1

    print("\n共修改 %d 条；有意保留 %d 组差异："
          % (changed, len(KEEP)))
    for k, why in KEEP.items():
        print("   %s: %s" % (k, why))

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return 0
    if not changed:
        return 0

    dic["_caseDriftKept"] = KEEP
    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
