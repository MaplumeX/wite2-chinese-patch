#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补充 75 条对话框/多行提示的翻译

来源：build/rea/gaps_authoritative.json 的 dialog 桶
      （Ghidra 已定义串 ∪ 严格对齐自扫描，已剔除假串）

约束（照 hook_text.c 的 fmt_match 契约）：
  1. `~` 是游戏内的换行符，位置必须保留
  2. `%d` `%s` `%u` `%%` 的数量和**顺序**必须与原文一致
     （fmt_match 按位置回填：vals[0], vals[1] ...）
  3. `____________` 下划线是列对齐填充，原样保留
  4. `<img %d>` / `<scl 50>` 是标记语言，不能动
  5. 纯数字/纯符号的组合（如 `~%d x %s`）无需翻译，跳过

大段说明（带制表符缩进的那种）不手写，按 `~` 切段后只替换文本、
制表符原样拼回，避免数错。

用法:
    python tools/add_dialogs.py --dry-run
    python tools/add_dialogs.py
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

# 普通条目：原文 -> 译文（含 ~ 与 % 占位符）
SIMPLE = {
    " ~ %s Losses:": " ~ %s 损失：",
    " ~ %s Escaped:": " ~ %s 逃脱：",
    " ~ ftr  %d,  bmr  %d ~": " ~ 战斗机  %d,  轰炸机  %d ~",
    " ~ %s Forces Attacking:": " ~ %s 进攻部队：",
    " ~ %s Forces Defending:": " ~ %s 防御部队：",
    " ~ %s Ground Support:  ": " ~ %s 对地支援：  ",
    " ~ ftr  %d,  rec  %d ~ ": " ~ 战斗机  %d,  侦察机  %d ~ ",
    " Supply Pct______%d ~ ~ ": " 补给百分比______%d ~ ~ ",
    " ~ %s Fighter Intercept:  ": " ~ %s 战斗机拦截：  ",
    " ~ Defending Forces %s  ~ ": " ~ 防御部队 %s  ~ ",
    "%s ~ ~Click to toggle sort": "%s ~ ~点击切换排序",
    "Exported %d %s row%s to~%s": "已导出 %d 条 %s 记录%s 至~%s",
    " ~ ftr %d,  bmr %d,  utl %d ": " ~ 战斗机 %d,  轰炸机 %d,  运输机 %d ",
    " ~ inf %d,  art %d,  afv %d ": " ~ 步兵 %d,  火炮 %d,  装甲车 %d ",
    " ~ men %d,  art %d,  afv %d ": " ~ 人员 %d,  火炮 %d,  装甲车 %d ",
    "Set loadout to all~%s in~%s?": "设为全部挂载~%s 于~%s？",
    "Men lost: %d~Men disabled: %d": "人员损失：%d~人员伤残：%d",
    "ftr  %d,  bmr  %d   utl  %d ~": "战斗机  %d,  轰炸机  %d   运输机  %d ~",
    " ~ ftr %d,  bmr %d,  utl %d ~ ": " ~ 战斗机 %d,  轰炸机 %d,  运输机 %d ~ ",
    " ~ inf %d,  art %d,  afv %d ~ ": " ~ 步兵 %d,  火炮 %d,  装甲车 %d ~ ",
    " ~ men  %d,  art  %d,  afv  %d ": " ~ 人员  %d,  火炮  %d,  装甲车  %d ",
    "%s population evacuates ~ to %s": "%s 人口撤离 ~ 至 %s",
    "Swap plane type ~ from %s to %s": "更换机型 ~ 从 %s 改为 %s",
    "Factories moving from %s ~ to %s": "工厂迁移 从 %s ~ 至 %s",
    " ~ men  %d,  art  %d,  afv  %d ~ ": " ~ 人员  %d,  火炮  %d,  装甲车  %d ~ ",
    "Mobilize Static Unit ? ~ ~ -%d AP": "动员静态部队？ ~ ~ -%d 行政点",
    " ~ ftr  %d,  bmr  %d ,  utl  %d ~ ": " ~ 战斗机  %d,  轰炸机  %d ,  运输机  %d ~ ",
    "Select units with ~ ~ %s (%s)~ ~%s": "选择部队，条件为 ~ ~ %s (%s)~ ~%s",
    "Set to relocate %s~from %s to %s ?": "设为转移 %s~从 %s 至 %s？",
    " ~ Battle  %d  of  %d ~ ~ %s Losses:": " ~ 战斗  %d / %d ~ ~ %s 损失：",
    "%s %s ~ takes command of ~ %s %s !!!": "%s %s ~ 接任指挥 ~ %s %s !!!",
    "Set loadout to all~%s in~%s air base?": "设为全部挂载~%s 于~%s 空军基地？",
    "Select air groups with~ ~ %s (%s)~ ~%s": "选择飞行队，条件为~ ~ %s (%s)~ ~%s",
    "Set Unit to Static ?~+ %d trucks/%d AP": "设为静态部队？~+ %d 卡车/%d 行政点",
    "Set loadout to all~%s in~%s Air Force?": "设为全部挂载~%s 于~%s 空军？",
    "Send unit to West Front ~ requires %d AP": "派遣部队至西线 ~ 需要 %d 行政点",
    "%s ~ (%s %s) ~ ~ %d/%d x %s ~ ~ Range: %d":
        "%s ~ (%s %s) ~ ~ %d/%d x %s ~ ~ 射程：%d",
    "%s %s commander ~ %s %s ~ is dismissed !!!": "%s %s 司令 ~ %s %s ~ 被解职 !!!",
    "%s ( %s )~Ready %d~Participation pct %d %%": "%s ( %s )~就绪 %d~参与百分比 %d %%",
    "Set loadout to all~%s in~%s air directive?": "设为全部挂载~%s 于~%s 空中指令？",
    "Motorize Unit? ~ ~ Cost - %d AP, %d trucks ": "摩托化部队？ ~ ~ 消耗 - %d 行政点, %d 卡车 ",
    "Build Fortified Zone?~Cost - %d Admin Points": "修建要塞地带？~消耗 - %d 行政点",
    "Batch export complete: %d files written to~%s": "批量导出完成：已写入 %d 个文件至~%s",
    "Click to change~%s~Air Unit Morale~%d Q%d: %d": "点击修改~%s~空军部队士气~%d Q%d: %d",
    " ~ Battle  %d  of  %d ~ ~ %s Forces Attacking:":
        " ~ 战斗  %d / %d ~ ~ %s 进攻部队：",
    " ~ No Admin points. Admin points: %d. Cost: %d": " ~ 行政点不足。行政点：%d。消耗：%d",
    "%s %s commander ~ %s %s ~ is promoted to %s!!!": "%s %s 司令 ~ %s %s ~ 晋升为 %s!!!",
    "Build Fortified Region?~Cost - %d Admin Points": "修建要塞地域？~消耗 - %d 行政点",
    "Click to change~%s~Naval Unit Morale~%d Q%d: %d": "点击修改~%s~海军部队士气~%d Q%d: %d",
    "Click to change~%s~Ground Unit Morale~%d Q%d: %d": "点击修改~%s~地面部队士气~%d Q%d: %d",
    "Close '%s' scenario?~All unsaved data will be lost":
        "关闭剧本 '%s'？~所有未保存的数据都将丢失",
    "%s ( %s )~Ready %d~Range %d~Participation pct %d %%":
        "%s ( %s )~就绪 %d~射程 %d~参与百分比 %d %%",
    '~ Too many "%s" units exist. Current: %d. Limit: %d':
        '~ "%s" 部队数量过多。当前：%d。上限：%d',
    "%s %s commander ~ %s %s ~ is arrested and EXECUTED !!!":
        "%s %s 司令 ~ %s %s ~ 被逮捕并处决 !!!",
    "Export %d selected data type(s) to CSV?~Files go to: %s":
        "导出 %d 个选定的数据类型到 CSV？~文件位置：%s",
    "<img %d> text after image~ ~<img %d><scl 50> another Line":
        "<img %d> 图片后的文字~ ~<img %d><scl 50> 另一行",
    "%s %s commander ~ %s %s ~ is promoted to %s (from %s to %s)!!!":
        "%s %s 司令 ~ %s %s ~ 晋升为 %s（由 %s 升为 %s）!!!",
    "Imported %d unit rows~%d column(s) not recognised and skipped: %s":
        "已导入 %d 行部队~%d 列无法识别并已跳过：%s",
    "Air HQ: %s %s~Range to Base: %d ~Range to Target: %d ~%s radius: %d":
        "空军司令部：%s %s~至基地距离：%d ~至目标距离：%d ~%s 半径：%d",
    "FONT TOTAL : %d fonts, %d glyphs, %d pages, ~%u MB atlas (UI scale %d%%)":
        "字体合计 : %d 个字体, %d 个字形, %d 页, ~%u MB 图集 (界面缩放 %d%%)",
    "Build Fortified Zone?~Current Units - %d  Limit - %d ~Cost - %d Admin Points":
        "修建要塞地带？~当前部队 - %d  上限 - %d ~消耗 - %d 行政点",
    "Build Fortified Region?~Current Units - %d  Limit - %d ~Cost - %d Admin Points":
        "修建要塞地域？~当前部队 - %d  上限 - %d ~消耗 - %d 行政点",
    "%s~ ~ %d/%d x %s~ Radius (hex) %d~ Experience  %d~ Morale        %d~ Fatigue       %d":
        "%s~ ~ %d/%d x %s~ 半径 (六角格) %d~ 经验  %d~ 士气        %d~ 疲劳       %d",
    "Import %d selected data type(s) from CSV?~Reads from: %s~Existing data will be overwritten.":
        "从 CSV 导入 %d 个选定的数据类型？~读取自：%s~现有数据将被覆盖。",
    "Date Available ~ From %d/%d~ to %d/%d____________~MODIFIERS~ ~Ammo Use: %d ~Fuel Use: %d ~Maneuver: %d  ~Endurance: %d ~Climb: %d ~Altitude: %d ~Max Speed: %d":
        "可用日期 ~ 从 %d/%d~ 至 %d/%d____________~修正项~ ~弹药消耗：%d ~燃料消耗：%d ~机动性：%d  ~续航：%d ~爬升：%d ~升限：%d ~最大速度：%d",
    " Political %d ~ Morale %d ~ Initiative %d ~ Admin %d ~ Mech %d ~ Infantry %d ~ Air %d ~ Naval %d ~ ~ Victories %d ~ Defeats %d ~ ~ Display leader detail / dismiss leader":
        " 政治 %d ~ 士气 %d ~ 主动性 %d ~ 行政 %d ~ 机械 %d ~ 步兵 %d ~ 空军 %d ~ 海军 %d ~ ~ 胜利 %d ~ 失败 %d ~ ~ 显示将领详情 / 解除将领职务",
}

# 大段说明：按 `~` 分段，只替换文字，保留每段开头的制表符/空格缩进
BLOCK = {
    "\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t": None,
}
BLOCK_TEXT = [
    (
        "amphib",
        "STANDARD - normal amphibious invasion rules",
        "有限",
    ),
]

# 带制表符的大段：用「原文分段 -> 译文分段」显式表
SEGMENTS = {
    "STANDARD - normal amphibious invasion rules": "标准 - 常规两栖登陆规则",
    "LIMITED - only hexes in the regions of Crimea, Georgia, and Krasnodar may be targets of an amphibious invasion(Ctrl - m shows all regions)":
        "有限 - 只有克里米亚、格鲁吉亚和克拉斯诺达尔地区的六角格可作为两栖登陆目标（Ctrl - m 显示所有地区）",
    "NONE - no amphibious invasions are allowed": "无 - 不允许任何两栖登陆",

    "STANDARD - normal leader deaths not caused by HQ displacement or air attack":
        "标准 - 常规的将领死亡（非因司令部位置调整或空袭所致）",
    "REDUCED - 1/2 the chance of leader deaths not caused by HQ displacement or air attack":
        "降低 - 将领死亡几率减半（非因司令部位置调整或空袭所致）",
    "NONE - no chance of leader deaths not caused by HQ displacement or air attack":
        "无 - 不会有将领死亡（非因司令部位置调整或空袭所致）",

    "STANDARD - normal combat delay movement point costs":
        "标准 - 常规的战斗迟滞移动力消耗",
    "REDUCED - movement point cost for each combat delay point is divided by 2 (and truncated)":
        "降低 - 每点战斗迟滞的移动力消耗减半（向下取整）",
    "MINIMAL - 0 movement point cost for combat delay points(still not able to attack a hex that has 9 combat delay)":
        "最小 - 战斗迟滞不消耗移动力（仍无法攻击有 9 点战斗迟滞的六角格）",

    "STANDARD - no change to campaign end conditions":
        "标准 - 战役结束条件不变",
    "EXTENDED PLAY - If the game is ending due to Sudden Victory / Sudden Loss / 31 December 1944 victory check, the game will not actually end.The victory screen will appear with the victory level showing as if it had ended, but extended play will be in effect and the game will continue normally upon closing the screen.":
        "延长游玩 - 若游戏因突然胜利／突然失败／1944 年 12 月 31 日胜利判定而即将结束，游戏实际上不会结束。胜利界面会显示并给出如同已结束的胜利等级，但会进入延长游玩模式，关闭该界面后游戏正常继续。",
    "NO EARLY END - the game ignores the Sudden Victory / Sudden Loss / 31 December 1944 victory check conditions completely.":
        "无提前结束 - 游戏完全忽略突然胜利／突然失败／1944 年 12 月 31 日胜利判定条件。",
}


def rebuild_block(orig):
    """把带 `~` 的大段按段翻译，保留每段原有的制表符/空格缩进"""
    parts = orig.split("~")
    out = []
    for seg in parts:
        m = re.match(r"^([\t ]*)", seg)
        indent = m.group(1)
        body = seg[len(indent):]
        if body in SEGMENTS:
            out.append(indent + SEGMENTS[body])
        elif body in SIMPLE:
            out.append(indent + SIMPLE[body])
        else:
            out.append(seg)      # 不是文本段（比如纯缩进），原样
    return "~".join(out)


def check(src, dst):
    """校验占位符数量与顺序"""
    a, b = PH.findall(src), PH.findall(dst)
    if a != b:
        return "占位符不一致: %s vs %s" % (a, b)
    if src.count("~") != dst.count("~"):
        return "~ 数量不一致: %d vs %d" % (src.count("~"), dst.count("~"))
    if dst.count("%%") != src.count("%%"):
        return "%% 数量不一致"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dic = json.load(open(DICT, encoding="utf-8"))
    keys = set(dic)

    cands = dict(SIMPLE)
    # 大段说明（含制表符的那些）：从权威清单里取原文
    gaps = json.load(open(os.path.join(ROOT, "build", "rea",
                                       "gaps_authoritative.json"),
                          encoding="utf-8"))
    for t in gaps.get("dialog", []):
        if "\t" in t:
            cands[t] = rebuild_block(t)

    ok, skipped, bad = [], [], []
    for src, dst in cands.items():
        if src in keys:
            skipped.append((src, "已在词典"))
            continue
        err = check(src, dst)
        if err:
            bad.append((src, err))
            continue
        ok.append((src, dst))

    print("候选 %d 条：可加 %d / 跳过 %d / 校验失败 %d\n"
          % (len(cands), len(ok), len(skipped), len(bad)))
    if bad:
        print("!! 校验失败：")
        for s, e in bad:
            print("   %s\n     %r" % (e, s[:100]))
        print()

    for src, dst in ok:
        n = len(src)
        print("  [%3d] %s\n        -> %s" % (n, src[:96].replace("\t", "→"),
                                            dst[:96].replace("\t", "→")))

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return 0 if not bad else 1

    for src, dst in ok:
        dic[src] = dst
    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=1)
    print("\n已写入 %s：%d -> %d 条" % (DICT, len(keys), len(dic)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
