#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补 70 条游戏内可见消息（`other` 桶里被埋很久的那批）

来源：build/rea/ingame_curated.csv（人工策展，见 tools/curate_ingame.py）
这批 **几乎全是 .rdata 里的 ASCII 字符串**，之前所有扫描器只扫 UTF-16，
所以从来没进过候选清单。

约束（同 add_dialogs.py）：
  1. `~` 是游戏内换行，数量必须一致
  2. `%d` `%s` `%x` `%.3s` `%%` 的数量和顺序必须一致
  3. 前后空格保留（有些是布局占位）

用法:
    python tools/add_ingame_msgs.py --dry-run
    python tools/add_ingame_msgs.py
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")

PH = re.compile(r"%[-+ #0-9.]*[a-zA-Z]")

TR = {
    # ---------- server：PBEM / 服务器对战 ----------
    "Unable to contact server!": "无法连接服务器！",
    "Unable to find game!": "找不到游戏！",
    "Unable to find challenge!": "找不到挑战！",
    "Can't play this game!": "无法进行此游戏！",
    "Failed to load a game!": "载入游戏失败！",
    "Unable to get steam user": "无法获取 Steam 用户",
    "Can't upload to server [E1]": "无法上传到服务器 [E1]",
    "Can't upload to server [E2]": "无法上传到服务器 [E2]",
    "Can't upload to server [E3]": "无法上传到服务器 [E3]",
    "Can't upload to server [E5]": "无法上传到服务器 [E5]",
    "Can't upload to server [E6]": "无法上传到服务器 [E6]",
    "Can't download from server [E4]": "无法从服务器下载 [E4]",
    "Unexpected error during a server operation": "服务器操作发生意外错误",
    "Wrong Turn Not Player's Turn": "回合错误：不是该玩家的回合",

    # ---------- admin：行政点 ----------
    "This order requires %d admin points!": "此命令需要 %d 行政点！",
    "Requires %d APs to mobilize this unit !!": "动员此部队需要 %d 行政点！！",
    "Not allowed - requires %d admin points": "不允许 - 需要 %d 行政点",
    "Not enough admin points (%d admin points are required)":
        "行政点不足（需要 %d 行政点）",
    "Not enough admin points (%d)": "行政点不足（%d）",
    "%s requires maintenance": "%s 需要维护",
    "%.20s requires maintenance": "%.20s 需要维护",

    # ---------- limit：建造上限 ----------
    "Can't build more than %d fort units": "要塞部队不能超过 %d 个",
    'Can\'t build more than %d "%s" units': '不能建造超过 %d 个 "%s" 部队',
    "  Can't get required amount of aircraft. Num %d Min %d":
        "  无法取得所需数量的飞机。现有 %d 最少 %d",
    "  Can't get required amount of escorting aircraft. Num %d Min %d":
        "  无法取得所需数量的护航飞机。现有 %d 最少 %d",

    # ---------- dialog：确认框 ----------
    "Set supply priority to %d for the selected units?":
        "将选定部队的补给优先级设为 %d？",
    "Delete Victory City (%s) ?": "删除胜利城市（%s）？",
    "Delete '%s' tag?": "删除标签 '%s'？",
    "Enter HEX coordinates:~x: %d - %d, y: %d - %d":
        "输入六角格坐标：~x: %d - %d, y: %d - %d",

    # ---------- tooltip：悬停提示（~ = 换行）----------
    "Click to set delay.~Unit will arrive: ~%d %.3s %d":
        "点击设置延迟。~部队将于 ~%d %.3s %d 到达",
    "Click to set delay.~Air group will arrive: ~%d %.3s %d":
        "点击设置延迟。~飞行队将于 ~%d %.3s %d 到达",
    "Click to set withdraw.~Airgroup will withdraw: ~%d %.3s %d":
        "点击设置撤离。~飞行队将于 ~%d %.3s %d 撤离",
    "Click to set delay.~Unit will arrive: ~%d %.3s %d (if %s moves first) ~%d %.3s %d (if %s moves second)":
        "点击设置延迟。~部队将于 ~%d %.3s %d（若 %s 先行）~%d %.3s %d（若 %s 后行）",
    "Click to set delay.~Air group will arrive: ~%d %.3s %d (if %s moves first) ~%d %.3s %d (if %s moves second)":
        "点击设置延迟。~飞行队将于 ~%d %.3s %d（若 %s 先行）~%d %.3s %d（若 %s 后行）",
    "Click to set withdraw.~Air group will withdraw: ~%d %.3s %d (if %s moves first) ~%d %.3s %d (if %s moves second)":
        "点击设置撤离。~飞行队将于 ~%d %.3s %d 撤离（若 %s 先行）~%d %.3s %d（若 %s 后行）",
    "Left click on map to set '%s' for '%s'":
        "在地图上左键点击为 '%s' 设置 '%s'",
    "Left click on map to select '%s' map x , y coordinates":
        "在地图上左键点击选择 '%s' 的地图 x , y 坐标",
    "Click to set '%s' filters": "点击设置 '%s' 的筛选条件",
    "Click to clear '%s' filters": "点击清除 '%s' 的筛选条件",
    "Click to edit %s": "点击编辑 %s",
    "Set Amphib Prep to %d": "两栖准备度设为 %d",
    "Set Combat Preparation to %d": "战斗准备度设为 %d",

    # ---------- notify：游戏内通知 ----------
    "Nation %s set as 'surrendered'": "国家 %s 已设为「已投降」",
    "%s %s is destroyed - unable to retreat (%d,%d)":
        "%s %s 被歼灭 - 无法撤退 (%d,%d)",
    "%s air group failed to transfer to %s": "%s 飞行队转移至 %s 失败",
    "%d units set to transfer to %s": "%d 支部队已设为转移至 %s",
    "%s will be created by a scheduled rename":
        "%s 将由计划中的改名创建",
    "%s %s in hex %d,%d is no longer frozen":
        "%s %s 在六角格 %d,%d 已不再冻结",
    "TURN %d. RUN EVENT: %s. Failed probability check.":
        "第 %d 回合。执行事件：%s。未通过概率判定。",
    "%d x AIRCRAFT set to profile '%s'":
        "%d 架飞机已设为档案 '%s'",
    " %s group FAILED to find AOG": " %s 编组未能找到作战群",

    # ---------- ui：控件标签 / 提示 ----------
    "Deselect ALL Air Groups": "取消选择全部飞行队",
    "Press 'ENTER' to exit (%d / %d)": "按 ENTER 退出（%d / %d）",
    "Delayed units are ignored": "延迟部队被忽略",
    "Set current and maximum MPs (unit maximum is %d)":
        "设置当前与最大移动力（部队上限为 %d）",
    "Set Turn (%s)": "设置回合（%s）",
    "Set Delay (%s)": "设置延迟（%s）",
    "Set Turn Expires (%s)": "设置回合失效（%s）",
    "Set cargo ships in %s": "设置 %s 中的货船数",
    "Set troop ships in %s": "设置 %s 中的运兵船数",
    "Click to enter search string": "点击输入搜索字符串",
    "Center Hex %d,%d": "居中六角格 %d,%d",

    # ---------- error：存档 / 载入失败 ----------
    "Failed to load scenario": "载入剧本失败",
    "Failed to save temporary file": "保存临时文件失败",
    "Failed to load game '%s'": "载入游戏 '%s' 失败",
    "Can't create folder '%s'": "无法创建文件夹 '%s'",
    "### ERROR : %s failed to save ###": "### 错误：%s 保存失败 ###",
    "### Failed to load %s ###": "### 载入 %s 失败 ###",
    "ERROR unable to save %s": "错误：无法保存 %s",
    "ERROR unable to open %s": "错误：无法打开 %s",
    "Ground data - failed intergrity check": "地面数据 - 完整性校验失败",
    "FAILED INTEGRITY CHECK!!! (GetBug Id %d)":
        "完整性校验失败！！！（GetBug Id %d）",
    "Failed to export. Error# %d": "导出失败。错误号 %d",
    "No city found: %s (%d)": "找不到城市：%s (%d)",
    "#Error: Duplicated OB entry - %d": "#错误：OB 条目重复 - %d",
    "ERROR unable to read from file %s": "错误：无法从文件 %s 读取",
    "Can't open a file. Error %x": "无法打开文件。错误 %x",
    "Can't open a file. 1 %x": "无法打开文件。1 %x",
    "Can't open a file. 2 %x": "无法打开文件。2 %x",
}


def check(src, dst):
    a, b = PH.findall(src), PH.findall(dst)
    if a != b:
        return "占位符不一致: %s vs %s" % (a, b)
    if src.count("~") != dst.count("~"):
        return "~ 数量不一致: %d vs %d" % (src.count("~"), dst.count("~"))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dic = json.load(open(DICT, encoding="utf-8"))
    keys = set(dic)

    ok, skip, bad = [], [], []
    for src, dst in TR.items():
        if src in keys:
            skip.append(src)
            continue
        err = check(src, dst)
        if err:
            bad.append((src, err))
            continue
        ok.append((src, dst))

    print("待加 %d 条：可加 %d / 跳过 %d / 校验失败 %d\n"
          % (len(TR), len(ok), len(skip), len(bad)))
    if bad:
        for s, e in bad:
            print("!! %s\n   %r" % (e, s[:100]))
        print()

    for src, dst in ok:
        print("  %s\n    -> %s" % (src[:92], dst[:92]))

    if args.dry_run:
        print("\n(dry-run)")
        return 0 if not bad else 1

    for src, dst in ok:
        dic[src] = dst
    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入：%d -> %d 条" % (len(keys), len(dic)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
