#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整清点 .rdata 字符串

思路反转：之前是"猜哪些是界面文本"（漏得多），现在改成
"精确列出哪些绝对不能翻"，剩下的全部当作待翻译候选。

这样漏的概率大幅降低，代价是待翻清单变长（但可以批量处理）。

用法:
    python tools/inventory.py            分类统计
    python tools/inventory.py --todo     输出待翻译清单
    python tools/inventory.py --blocked  输出被排除的（核对是否误排）
    python tools/inventory.py --csv      导出 todo CSV
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

# ---------------------------------------------------------------- 排除规则
# 每一条都注明原因，方便核对

BLOCK_EXACT = {
    # ---- 地形/天气/资源枚举：用于查找 //Tiles// 下的图形 ----
    "WATER", "CLEAR", "ROUGH", "MOUNTAIN", "SWAMP", "WOODS", "FOREST",
    "CITY", "URBAN", "BOCAGE", "TUNDRA", "DESERT", "SAND", "IMPASSABLE",
    "POLDER", "HVY_URBAN", "RIVER", "RAIL", "ROAD", "BORDER", "COAST",
    "TOWN", "PORT", "AIRFIELD", "MAJOR", "MINOR",
    "H_MUD", "L_MUD", "H_SNOW", "L_SNOW", "MUD", "SNOW",
    "CIT", "PRI*", "TPI*", "XXXX",

    # ---- 配置键名：游戏读设置文件用，改了会丢设置 ----
    "SoundFx", "SfxGroupVolume", "FontColor", "RailStatus", "AirExecLog",
    "EventLog", "CombatMessages", "BattleSites", "UnitRename",
    "AirGroupRename", "MovePoints", "ScenarioText", "CreditText",
    "FlakIntensity", "PortCapacity", "RailyardCapacity", "WitePedia",
    "QuickSave", "DebugLog", "AirGroups", "SERVER_GAMES", "EDIT_MAIN",
    "AutoPtrl", "FollowHq", "GndClass", "HigherHQ", "InfoLink",
    "LastYear", "LoadCost", "MaxSpeed", "NightFly", "OilStore",
    "ProdType", "RadarDev", "SidesNum", "TopArmor", "FormSize",
    "GameOver", "AbDamage", "AGeneric", "AntiSoft", "FuelR", "SFuel",
    "FuelN", "FuelUse", "FuelStore", "SortieFuel", "Ws0_SortieFuel",
    "Ws1_SortieFuel", "Ws2_SortieFuel", "Ws3_SortieFuel", "Ws4_SortieFuel",
    "Ws0_Year", "Ws1_Year", "Ws2_Year", "Ws3_Year", "Ws4_Year",
    "PortValue", "RailUse", "BOMB_TO", "DAMD", "AOG", "AOGs",
    "HEAD", "POST", "KILL", "WARN", "LOSS", "SHA1", "FQDN", "TE",
    "AAir", "AArm", "APri", "SPri", "Airb", "Aslt", "Attk", "Bmr",
    "Crps", "Depl", "Dest", "DisC", "Disb", "Draw", "DtHQ", "Eccs",
    "FArm", "FChF", "FJG", "Fate", "Fort", "Free", "Frzn", "Ftr",
    "Gone", "HEAT", "HVAP", "Hide", "Icon", "Land", "MaxC", "MenR",
    "Mnvr", "Mort", "NJG", "Near", "OilR", "Prep", "Prim", "Rckt",
    "Req%", "ResR", "Role", "SArm", "SKG", "StG", "Sup%", "SupL",
    "SupN", "SupR", "TArm", "ToeM", "Trvl", "TtOB", "VW-L", "Veh%",
    "VehN", "VehR", "rdy", "dam", "Trn", "Reinf", "Trans", "SPGun",
    "MtInf", "AirLnd", "Cav", "Art", "Eng", "Fin", "Ger", "Hun",
    "Ita", "Rem", "Mot", "Rum", "Disb", "Arriv", "Airb", "Fighter",
    "Koluft", "Fliegerfuehrer",
}

# 正则排除
BLOCK_PATTERNS = [
    (r"^//", "资源路径"),
    (r"^(gui/|Gui/)", "资源路径"),
    (r"^(dat\\|Dat\\)", "资源路径"),
    (r"\.(bmp|jpg|tga|sav|psv|dat|csv|scn|txt|ini|json|dll|exe)$", "文件名"),
    (r"^[a-z][a-z0-9]*\.[a-z]+$", "文件名"),
    (r"%[-0-9.*]*[sdifxc]", "格式串"),
    (r"^[a-z]+[A-Z]", "驼峰标识符"),
    (r"^[a-z_]{2,}$", "小写标识符"),
    (r"^_", "内部前缀"),
    (r"^(Enter|Input|Eneter) ", "编辑器输入提示"),
    (r"^\*\*\*\*\*", "编辑器校验输出"),
    (r"^\d{4} [A-Z]", "剧本代号"),
    (r"^(shift|ctrl|alt)-", "热键名"),
    (r"^<", "占位符"),
    (r"^(If-|Accept|Content-|Cache-|Connection|ETag|Expires|Host|"
     r"Authorization|Allow|Range|Referer|Server|Set-|Transfer-|"
     r"Upgrade|Vary|Via|Warning|WWW-|Proxy-|Max-|Retry-|Trailer|"
     r"Location|Pragma|Cookie|Link|Origin|Access-|X-|Expect|"
     r"Keep-Alive|Last-Modified|User-Agent|Do-Not-Track)", "HTTP头"),
    (r"^(application|text|multipart|message|image|audio|video)/", "MIME类型"),
    (r"^[\d\s:/\-.,%=+*()]+$", "纯数字"),
    (r"^(HH:mm:ss|MM/dd/yy|dd/MM/yy|yyyy)", "时间格式"),
    (r"^\s+$", "空白"),
    (r"^[#*]{2,}", "分隔符"),
    (r"^[A-Z]{2,}\d*$", "全大写代号"),          # 但下面有白名单
    (r"^\d", "数字开头"),
    (r"^[^A-Za-z]*$", "无字母"),
]

# 全大写但有意义的界面标签（不被"全大写代号"规则排除）
UPPER_OK = {
    "AMMO", "AUTO", "AXIS", "BASE", "BUILT", "CHAIN", "CTRL", "DAMAGE",
    "DELETE", "DELAY", "EVENTS", "FACE", "FROM", "GOOD", "GUARDS", "MAIN",
    "MARKED", "MONTH", "MOTOR", "MULTI", "NAVAL", "NATION", "NEXT",
    "PLAYER", "POOL", "POOR", "RANK", "SERVER", "SHIPS", "SOVIET",
    "TAGGED", "TARGET", "TERR", "TILE", "TURN", "TURR", "YEAR", "AFTER",
    "ARMOR", "BOMBER", "NONE", "UNITS", "CONDITION", "INSERT", "PASTE",
    "COPY", "CLOSE", "DOWN", "FIND", "LOAD", "SAVE", "TRUE", "FALSE",
    "VALID", "LIMITED", "INSTANT", "SPECIAL", "SUMMARY", "REGIONS",
    "REMOVED", "REDUCED", "TRAINED", "REACTIVATE", "PRODUCTION",
    "SUPERIORITY", "DOCTRINES", "ARMAMENTS", "VEHICLES", "SYNTH",
    "INDUSTRY", "MANPOWER", "RESOURCE", "RAILYARD", "AIRFIELD",
    "CONTAINS", "CONDITION", "CONFLICT", "RESET", "INSERT", "DELETE",
    "CANCEL", "CONFIRM", "EXECUTE", "TRANSLATE", "MULTIPLY", "SUBTRACT",
    "DIVIDE", "PERCENT", "RANDOMIZE", "RESIZE", "MARK", "UNMARK",
    "OUTLINE", "ITALIC", "BOLD", "ALPHA", "METRIC", "IMPERIAL",
    "BLIZZARD", "HARSH", "MILD", "MODERATE", "AVERAGE", "EXCELLENT",
    "GOOD", "POOR", "SEVERE", "EXTREME", "NORMAL", "STANDARD",
}


def load_strings():
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
    pat = re.compile(rb"(?:[\x20-\x7e]\x00){2,}")
    out, seen = [], set()
    for m in pat.finditer(body):
        s = m.group().decode("utf-16-le")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def classify(s):
    """返回 (原因, 是否可翻)"""
    if s in BLOCK_EXACT:
        return "禁止项", False
    if s in UPPER_OK:
        return "全大写标签", True
    for pat, reason in BLOCK_PATTERNS:
        if re.search(pat, s):
            return reason, False
    if not re.search(r"[A-Za-z]{2,}", s):
        return "无字母", False
    if len(s) < 2:
        return "过短", False
    return "可翻", True


def main():
    strings = load_strings()
    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)
    have = set(k.lower() for k in dic if not k.startswith("_"))

    todo, blocked = [], []
    stats = {}
    for s in strings:
        reason, ok = classify(s)
        stats[reason] = stats.get(reason, 0) + 1
        if ok and s.lower() not in have:
            todo.append(s)
        elif not ok:
            blocked.append((s, reason))

    todo.sort(key=lambda x: (len(x), x))

    if "--blocked" in sys.argv:
        for s, r in sorted(blocked, key=lambda x: x[1]):
            print("%-14s %r" % (r, s))
        return 0

    if "--todo" in sys.argv:
        if "csv" in sys.argv:
            out = os.path.join(ROOT, "inventory_todo.csv")
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["原文", "译文"])
                for s in todo:
                    w.writerow([s, ""])
            print("导出 %d 条 -> %s" % (len(todo), out))
        else:
            for s in todo:
                print(repr(s))
        return 0

    print("总字符串 %d" % len(strings))
    print("分类:")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print("  %-16s %d" % (k, v))
    print("\n待翻译 %d 条" % len(todo))
    print("已排除 %d 条" % len(blocked))
    print("\n用 --todo 输出待翻清单，--blocked 核对排除是否合理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
