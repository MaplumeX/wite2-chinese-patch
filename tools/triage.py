#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精确分类 .rdata 字符串，找出该翻但没翻的界面文本

比 scan_ui.py 更细，专门处理"混杂区"——之前简单规则会把
Attacker（该翻）和 AutoPtrl（不该翻）混在一起。

用法:
    python tools/triage.py            分类统计
    python tools/triage.py --todo     输出该翻的（可直接粘进翻译脚本）
    python tools/triage.py --todo csv 导出 CSV
"""

import csv
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"

# 明确不能翻的（改了会出错）
BLOCK = {
    # 地形/天气/资源枚举
    "WATER", "CLEAR", "ROUGH", "MOUNTAIN", "SWAMP", "WOODS", "FOREST",
    "CITY", "URBAN", "BOCAGE", "TUNDRA", "DESERT", "SAND", "IMPASSABLE",
    "POLDER", "HVY_URBAN", "RIVER", "RAIL", "ROAD", "BORDER", "COAST",
    "TOWN", "PORT", "AIRFIELD", "MAJOR", "MINOR",
    "H_MUD", "L_MUD", "H_SNOW", "L_SNOW", "MUD", "SNOW",
    # 配置键名 / 内部标识符
    "SoundFx", "SfxGroupVolume", "FontColor", "RailStatus", "AirExecLog",
    "EventLog", "CombatMessages", "BattleSites", "UnitRename",
    "AirGroupRename", "MovePoints", "ScenarioText", "CreditText",
    "FlakIntensity", "PortCapacity", "RailyardCapacity", "WitePedia",
    "QuickSave", "DebugLog", "AirGroups", "SERVER_GAMES", "EDIT_MAIN",
    "AutoPtrl", "FollowHq", "GndClass", "HigherHQ", "InfoLink",
    "LastYear", "LoadCost", "MaxSpeed", "NightFly", "OilStore",
    "ProdType", "RadarDev", "SidesNum", "TopArmor", "FormSize",
    "GameOver", "AbDamage", "AGeneric", "Airborne", "AntiSoft",
    "Executed", "Observed", "Modified", "Unlocked", "Accepted",
    "Conflict", "Engineer", "Industry", "Official", "Recruits",
    "Schedule", "Security", "Squadron", "Brigades", "Missions",
    # 部队代号
    "AOG", "ChF", "KBF", "IAD", "IAK", "SAD", "SAK", "BAD", "BAK",
    "DBAD", "DBAK", "GBAD", "GBAK", "GIAD", "GIAK", "NBAD", "TBAD",
    "TBAK", "ShAD", "ShAK", "GShAD", "GShAK", "PShAD", "StG", "JG",
    "KG", "NJG", "ZG", "TG", "SKG", "NSGr", "NAGr", "FAGr", "Koluft",
}

# 该翻的
WHITELIST = {
    "Check Date": "检查日期",
    "Initiative Player": "主动权玩家",
    "Initiative Player : %s": "主动权玩家：%s",
    "Initiative": "主动性",
    "Initiative rating": "主动性评级",
    "Lose Initiative %d": "失去主动权 %d",
    "Initiative switch check dates": "主动权切换检查日期",
    "Initiative switch check %%": "主动权切换检查 %%",
    "Enter Day for Initiative Switch Start (1..%d)": "输入主动权切换开始日（1..%d）",
    "Enter Month for Initiative Switch Start": "输入主动权切换开始月",
    "Enter Year for Initiative Switch Start": "输入主动权切换开始年",
    "Enter Day for Initiative Switch End (1..%d)": "输入主动权切换结束日（1..%d）",
    "Enter Month for Initiative Switch End": "输入主动权切换结束月",
    "Enter Year for Initiative Switch End": "输入主动权切换结束年",
    "Enter Initiative Switch Check Percentage": "输入主动权切换检查百分比",
    "Fuel production": "燃料生产",
    "Fuel requirements": "燃料需求",
    "Fuel storage": "燃料储备",
    "Fuel use": "燃料使用",
    "Fuel Percentage": "燃料百分比",
    "Fuel Received": "燃料接收",
    "Fuel Need tons": "燃料需求（吨）",
    "Fuel tons": "燃料（吨）",
    "Synthetic Fuel production": "合成燃料生产",
    "Oil Production": "石油生产",
    "Resource Production": "资源生产",
    "Production Multipliers": "生产倍率",
    "Production Usage %": "生产使用 %",
    "Set Soviet Production Usage %": "设置苏联生产使用 %",
    "Set Axis and Axis Allies Production Usage %": "设置轴心及轴心盟国生产使用 %",
    "Enter %s Production Usage %% (1-100)": "输入%s生产使用 %%（1-100）",
    "Enter Axis Production Usage % (1-100)": "输入轴心生产使用 %（1-100）",
    "Enter Axis Allies Production Usage % (1-100)": "输入轴心盟国生产使用 %（1-100）",
    "Enter W Allies Production Usage % (1-100)": "输入西方盟军生产使用 %（1-100）",
    "Weeks in Production:": "生产周数：",
    "Show Production for %s.": "显示%s的生产",
    "Normal": "正常",
    "Accuracy": "精度",
    "Activate": "激活",
    "Airborne": "空降",
    "Airfield": "机场",
    "Altitude": "高度",
    "Anti-Air": "防空",
    "Attacker": "攻方",
    "Axis (1)": "轴心（1）",
    "Chain ID": "指挥链ID",
    "City Hex": "城市六角格",
    "Corps HQ": "军司令部",
    "Crp/Army": "军/集团军",
    "Damage": "损伤",
    "Damage %": "损伤 %",
    "Defeats:": "失败次数：",
    "End Date": "结束日期",
    "Far East": "远东",
    "Front HQ": "方面军司令部",
    "HC/Front": "最高统帅部/方面军",
    "HQ Chain": "指挥链",
    "HQ Types": "司令部类型",
    "HQ Units": "司令部部队",
    "Hvy Snow": "大雪",
    "In Trans": "转移中",
    "Load Out": "挂载",
    "Map X, Y": "地图 X, Y",
    "Max Load": "最大载弹",
    "New Name": "新名称",
    "Open Map": "打开地图",
    "Plt Type": "飞行员类型",
    "SS Elite": "党卫军精锐",
    "Supply %": "补给 %",
    "Total VP": "总胜利点",
    "Used Veh": "已用车辆",
    "high cmd": "高级指挥",
    "Air HQ: ": "空军司令部：",
    "Damage  ": "损伤  ",
    "( size ": "（规模 ",
    "Saturday": "星期六",
    "Thursday": "星期四",
}


def load_ui_strings():
    exe = os.path.join(GAME, "WarInTheEast2.exe")
    d = open(exe, "rb").read()
    e = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, e + 6)[0]
    optsz = struct.unpack_from("<H", d, e + 20)[0]
    secs = {}
    for i in range(nsec):
        o = e + 24 + optsz + 40 * i
        name = d[o:o + 8].rstrip(b"\0").decode()
        vs, va, rs, ra = struct.unpack_from("<IIII", d, o + 8)
        secs[name] = (va, vs, ra, rs)
    rd_va, _, rd_raw, rd_rs = secs[".rdata"]
    body = d[rd_raw:rd_raw + rd_rs]
    pat = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")
    out, seen = [], set()
    for m in pat.finditer(body):
        s = m.group().decode("utf-16-le")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def classify(s):
    """返回 (类别, 是否该翻)"""
    if s in BLOCK:
        return "禁止", False

    # 技术串
    if re.search(r"(%[-0-9.*]*[sdifxc]|\\|\.(cpp|h|dat|csv|tga|bmp|scn|txt|json|ini)$)", s):
        return "技术串", False
    if re.match(r"^(//|gui/|Gui/|dat\\|Dat\\)", s):
        return "路径", False
    if re.match(r"^(Enter|Input|Eneter) ", s):
        return "编辑器输入", False
    if re.match(r"^\*\*\*\*\*", s):
        return "编辑器校验", False
    if re.match(r"^\d{4} [A-Z]", s):
        return "剧本代号", False
    if re.match(r"^(shift|ctrl|alt)-", s):
        return "热键名", False
    if re.match(r"^<", s):
        return "占位符", False
    if re.fullmatch(r"[A-Z]{2,6}", s) and s in BLOCK:
        return "禁止", False

    # 驼峰标识符（小写后接大写）
    if re.match(r"^[a-z]+[A-Z]", s) and " " not in s:
        return "驼峰标识符", False
    # 全小写带下划线
    if re.fullmatch(r"[a-z_]+", s):
        return "小写标识符", False
    # HTTP 头
    if re.match(r"^(If-|Accept|Content|Cache|Connection|ETag|Expires|Host|"
                r"Authorization|Allow|Range|Referer|Server|Set-|Transfer-|"
                r"Upgrade|Vary|Via|Warning|WWW-|Proxy-|Max-|Retry-|Trailer|"
                r"Location|Pragma|Cookie|Link|Origin|Access-|X-|Expect)", s):
        return "HTTP头", False
    # 纯数字/时间格式
    if re.fullmatch(r"[\d\s:/\-.,%]+", s):
        return "数字格式", False
    if re.fullmatch(r"(HH:mm:ss|MM/dd/yy|dd/MM/yy)", s):
        return "时间格式", False
    # 无字母
    if not re.search(r"[A-Za-z]", s):
        return "无字母", False

    # 玩家可见：有空格的多词短语，或首字母大写
    if s in WHITELIST:
        return "该翻", True
    if " " in s and len(s) >= 4:
        return "该翻", True
    if s[0].isupper() and len(s) >= 4:
        return "该翻", True
    if s.endswith(":"):
        return "该翻", True

    return "待定", False


def main():
    strings = load_ui_strings()
    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)
    keys_lower = set(k.lower() for k in dic if not k.startswith("_"))

    todo = []
    stats = {}
    for s in strings:
        cat, should = classify(s)
        stats[cat] = stats.get(cat, 0) + 1
        if should and s.lower() not in keys_lower:
            todo.append(s)

    todo.sort(key=lambda x: (len(x), x))

    if "--todo" in sys.argv:
        if "csv" in sys.argv:
            out = os.path.join(ROOT, "triage_todo.csv")
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["原文", "建议译文"])
                for s in todo:
                    w.writerow([s, WHITELIST.get(s, "")])
            print("导出 %d 条 -> %s" % (len(todo), out))
        else:
            print("# 该翻 %d 条" % len(todo))
            for s in todo:
                sug = WHITELIST.get(s, "")
                print("%r: %r," % (s, sug))
        return 0

    print("分类统计:")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print("  %-12s %d" % (k, v))
    print("\n该翻但未翻: %d 条" % len(todo))
    print("（用 --todo 输出，--todo csv 导出）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
