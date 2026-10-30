#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部队名（OB Suffix）组合式翻译

部队显示名 = OBs.csv 的 Name + Suffix，例如
    1st + " Motorized Infantry Regiment" = "1st Motorized Infantry Regiment"

Name 部分（数字、番号、地名）不翻，Suffix 部分是兵种/编制术语，需要翻。

Suffix 是组合式的：[国籍] [修饰] [兵种] [编制]
中英语序一致，逐词替换再拼接即可：
    "Army Mot. Pioneer Brigade" -> 集团军 + 摩托化 + 工兵 + 旅

用法:
    python tools/unit_names.py check     统计覆盖率
    python tools/unit_names.py gen       生成译文到 _unit_terms.json
    python tools/unit_names.py apply     写入 zh_dict.json
    python tools/unit_names.py review    列出无法组合的条目
"""

import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
OBS = os.path.join(GAME, "Dat", "CSV", "Generic", "OBs.csv")
UNITS = os.path.join(GAME, "Dat", "CSV", "Generic", "Units.csv")
DICT = os.path.join(ROOT, "zh_dict.json")
OUT = os.path.join(ROOT, "_unit_terms.json")

# 逐词对照表。中文军事术语里，国籍在前、修饰居中、兵种在编制之前，
# 与英文语序一致，所以按 token 顺序拼接即可。
TERMS = {
    # ---- 国籍 / 阵营前缀 ----
    "Ital.": "意大利", "Rum.": "罗马尼亚", "Hun.": "匈牙利",
    "Bul.": "保加利亚", "Fin.": "芬兰", "Slov.": "斯洛伐克",
    "Croat.": "克罗地亚", "Croatian": "克罗地亚",
    "BR": "英国", "US": "美国", "CA": "加拿大", "AU": "澳大利亚",
    "AUS": "澳大利亚", "FF": "自由法国", "AI": "盟军意大利",
    "PO": "波兰", "NZ": "新西兰", "BE": "比利时", "IN": "印度",
    "CW": "英联邦", "CZ": "捷克", "GR": "希腊", "NE": "荷兰",
    "SA": "南非", "SU": "苏联", "RSI": "意大利社会共和国",
    "DMA": "意黑衫", "DMC": "意黑衫", "DMM": "意黑衫", "DMO": "意黑衫",
    "CCNN": "黑衫军", "Yug.": "南斯拉夫",

    # ---- 军种 / 组织前缀 ----
    "LW": "空军", "SS": "党卫军", "NKVD": "内务部", "PVO": "防空",
    "KM": "海军", "FJ": "伞兵", "HG": "赫尔曼·戈林", "HJ": "希特勒青年团",
    "R.A.D.": "帝国劳工服务", "NKPS": "交通人民委员部",
    "RVGK": "最高统帅部预备队", "Gep.": "装甲", "Sep.": "独立",
    "Separate": "独立", "zbV": "特设", "Wach": "卫戍", "Sperr": "封锁",
    "Ost": "东方", "Afrika": "非洲",

    # ---- 编制级别 ----
    "Battalion": "营", "Bataillon": "营", "Abteilung": "营",
    "Regiment": "团", "Regim": "团", "Brigade": "旅",
    "Division": "师", "Company": "连", "Battery": "炮兵连",
    "Corps": "军", "Korps": "军", "Corpo": "军",
    "Army": "集团军", "Group": "集群", "Kampfgruppe": "战斗群",
    "Squadron": "中队", "Platoon": "排", "Detachment": "支队",
    "Legion": "军团", "Fortress": "要塞", "Zone": "地带",
    "Base": "基地", "Region": "区", "District": "区",
    "Tower": "塔", "HQ": "司令部", "Command": "司令部",
    "Komendatura": "警备司令部", "Force": "部队", "Task": "特遣",
    "Commando": "突击队", "Front": "方面军", "AGRA": "皇家野战炮兵群",
    "GACA": "军属野战炮兵群", "Tactical": "战术",

    # ---- 兵种 ----
    "Infantry": "步兵", "Rifle": "步兵", "Artillery": "炮兵",
    "Cavalry": "骑兵", "Cavalry-Mechanized": "骑兵机械化",
    "Armored": "装甲", "Armoured": "装甲", "Panzer": "装甲",
    "Tank": "坦克", "Flak": "高炮", "AA": "防空", "AAA": "防空",
    "Anti-Aircraft": "防空", "Anti-Tank": "反坦克", "Anti-tank": "反坦克",
    "AT-Rifle": "反坦克枪", "Engineer": "工兵", "Engineer-Sapper": "工兵",
    "Pioneer": "工兵", "Construction": "建设", "Security": "警备",
    "Mountain": "山地", "Parachute": "伞兵", "Airborne": "空降",
    "Airlanding": "机降", "Luftlande": "机降", "Air-Portable": "可空运",
    "Marine": "海军陆战", "Mortar": "迫击炮", "Howitzer": "榴弹炮",
    "Gun": "火炮", "Guard": "近卫", "Guards": "近卫",
    "Jaeger": "猎兵", "Skijaeger": "滑雪猎兵", "Ski-Jaeger": "滑雪猎兵",
    "Grenadier": "掷弹兵", "Panzergrenadier": "装甲掷弹兵",
    "Volksgrenadier": "国民掷弹兵", "Fusilier": "燧发枪兵",
    "Fusiliers": "燧发枪兵", "Field": "野战", "Border": "边防",
    "Frontier": "边防", "Fortified": "筑垒", "Coastal": "海岸",
    "Mechanized": "机械化", "Mechanzied": "机械化", "Motorized": "摩托化",
    "Mot.": "摩托化", "Semi-Mot.": "半摩托化", "Bicycle": "自行车",
    "Motorcycle": "摩托车", "Ski": "滑雪", "Recon": "侦察",
    "Reconnaissance": "侦察", "Recce": "侦察", "Rocket": "火箭炮",
    "Cannon": "加农炮", "Destroyer": "驱逐", "Stug": "突击炮",
    "Sturm": "突击", "Sturmpanzer": "突击炮",
    "Sturmmoerser": "突击迫击炮", "Werfer": "火箭炮",
    "Nebelwerfer": "火箭炮", "Flame": "喷火", "Flamethrower": "喷火",
    "Flammpanzer": "喷火坦克", "Fallschirm": "伞兵",
    "Fallschirmjaeger": "伞兵猎兵", "Schuetzen": "射手",
    "Landesschuetzen": "国土射手", "Panzerjaeger": "装甲猎兵",
    "Panzerjagd": "装甲猎兵", "Volks": "国民", "Ersatz": "补充",
    "Replacement": "补充", "Reserve": "预备", "Training": "训练",
    "Militia": "民兵", "Partisan": "游击", "Police": "警察",
    "Polizei": "警察", "Labor": "劳工", "Rail": "铁路",
    "Railroad": "铁路", "Railway": "铁路", "Road": "道路",
    "Air": "航空", "Naval": "海军", "Amphibious": "两栖",
    "Aviation": "航空", "Fighter": "战斗机", "Escort": "护航",
    "Special": "特种", "Static": "静态", "Garrison": "守备",
    "Outpost": "前哨", "Siege": "攻城", "Shock": "突击",
    "Assault": "突击", "Mixed": "混合", "Mobile": "机动",
    "Fast": "快速", "High": "高", "Super": "超级", "Heavy": "重型",
    "Light": "轻型", "Medium": "中型", "Royal": "皇家",
    "Military": "军事", "Service": "勤务", "Support": "支援",
    "Operating": "作业", "Power": "动力", "Remount": "补充马匹",
    "March": "行军", "Defense": "防御", "Volunteer": "志愿",
    "Cossack": "哥萨克", "Hussar": "骠骑兵", "Ranger": "游骑兵",
    "Bersaglieri": "狙击兵", "Alpina": "阿尔卑斯", "Celere": "快速",
    "Legere": "轻装", "Tabor": "塔博尔", "Marocains": "摩洛哥",
    "Mecanique": "机械化", "Pacifique": "太平洋", "Arab": "阿拉伯",
    "Libyan": "利比亚", "Tunisian": "突尼斯", "Gurkha": "廓尔喀",
    "MG": "机枪", "AAMG": "高射机枪", "MG-Artillery": "机枪炮兵",
    "Auto-Wpns": "自动武器", "Launcher": "发射器", "Pack": "驮载",
    "Chemical": "化学", "Breakthrough": "突破", "Expeditionary": "远征",
    "Schnelle": "快速", "Flieger": "航空", "PzG": "装甲掷弹兵",
    "TD": "坦克歼击", "CDL": "探照坦克", "FKL": "遥控",

    # ---- 专名（音译）----
    "Brandenburg": "勃兰登堡", "Brandenburger": "勃兰登堡",
    "Rommel": "隆美尔", "Ewert": "埃韦特", "Fullriede": "富尔里德",
    "Sissi": "西西", "Sahrien": "萨赫里安",
    "Northumberland": "诺森伯兰", "Moscow": "莫斯科",

    # ---- 补充（审查未识别项后加的）----
    "SP": "自行", "RR": "铁路", "GS": "通用勤务",
    "leFH": "轻型野战榴弹炮", "sFH": "重型野战榴弹炮",
    "MO": "摩洛哥", "MNR": "共和海军",
    "Car": "车", "Combat": "战斗",
    "Luftwaffe": "空军", "Nr.1": "第1", "Nr.2": "第2",
    "Tier": "型", "Shtat": "编制", "Reduced": "缩减",
    "German": "德国", "City": "城市", "Group": "集群",
    "BM": "BM", "BZ": "BZ", "CSTT": "CSTT",
    # RD / RSK / SFK / K3 / 'L' 是部队代号，没有可靠释义，
    # 保留原样比猜错安全（游戏里它们就是代号，不是英文单词）。

    # ---- 标点 / 修饰 ----
    "(-)": "（减）", "(+)": "（加）", "(SP)": "（自行）",
    "et": "与", "du": "", "d'Afrique": "非洲", "Franc": "自由",
}

# 多词短语。必须先于单词匹配，否则 "Machine Gun" 会被拆成
# Machine(机枪) + Gun(火炮) 变成"机枪火炮"。
PHRASES = [
    ("SP Field Artillery", "自行野战炮兵"),
    ("SP Heavy Panzerjaeger", "自行重型装甲猎兵"),
    ("SP Panzerjaeger", "自行装甲猎兵"),
    ("SP Infantry Gun", "自行步兵炮"),
    ("SP Light Flak", "自行轻型高炮"),
    ("Machine Gun", "机枪"),
    ("MG-Artillery", "机枪炮兵"),
    ("Armoured Car", "装甲车"),
    ("Armored Car", "装甲车"),
    ("Combat Engineer", "战斗工兵"),
    ("Field Artillery", "野战炮兵"),
    ("Infantry Gun", "步兵炮"),
    ("Heavy Howitzer", "重型榴弹炮"),
    ("Light Howitzer", "轻型榴弹炮"),
    ("Medium Howitzer", "中型榴弹炮"),
    ("Anti-tank Gun", "反坦克炮"),
    ("Anti-Tank Gun", "反坦克炮"),
    ("Motorized Infantry", "摩托化步兵"),
    ("Heavy Panzerjaeger", "重型装甲猎兵"),
    ("Air Base", "航空基地"),
    ("Border Guard", "边防"),
    ("Rocket Launcher", "火箭炮"),
    ("Corps Machine Gun", "军属机枪"),
    ("Corps MG", "军属机枪"),
    ("Army Artillery", "集团军属炮兵"),
    ("Artillery Battery", "炮兵连"),
    ("Reduced Shtat", "缩减编制"),
    ("Panzer Group", "装甲集群"),
]

# 括号里的口径/规格，统一换成中文括号 + 毫米
def conv_paren(tok):
    if not (tok.startswith("(") and tok.endswith(")")):
        return None
    inner = tok[1:-1]
    if inner == "SP":
        return "（自行）"
    if inner.endswith("mm") and inner[:-2].replace(".", "").isdigit():
        return "（%s毫米）" % inner[:-2]
    if inner.endswith("in") and inner[:-2].replace(".", "").isdigit():
        return "（%s英寸）" % inner[:-2]
    return None


def translate(suffix):
    """先替换多词短语，再逐 token 翻译。返回 (译文, 未识别 token 列表)"""
    work = suffix
    ph = {}
    for i, (en, zh) in enumerate(PHRASES):
        marker = "%d" % i
        if en in work:
            work = work.replace(en, marker)
            ph[marker] = zh
    # 标记两侧补空格，保证按空格切分时独立成 token
    for marker in ph:
        work = work.replace(marker, " " + marker + " ")

    toks = work.split()
    out = []
    unknown = []
    for idx, tok in enumerate(toks):
        last = (idx == len(toks) - 1)
        # "Corps Artillery Regiment" 里的 Corps 是"军属"，
        # 而 "PO Corps" 里的 Corps 就是"军"本身。看它是否在末尾。
        if not last and tok == "Corps":
            out.append("军属")
            continue
        if not last and tok == "Army":
            out.append("集团军属")
            continue
        if tok in ph:
            out.append(ph[tok])
            continue
        if tok in TERMS:
            out.append(TERMS[tok])
            continue
        p = conv_paren(tok)
        if p is not None:
            out.append(p)
            continue
        if tok[0].isdigit() or tok in ("1st", "2nd", "3rd", "4th"):
            out.append(tok)
            continue
        unknown.append(tok)
        out.append(tok)
    return "".join(out), unknown


def load_suffixes():
    raw = open(OBS, "rb").read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    s = sorted({(r.get("Suffix") or "") for r in rows})
    return [x for x in s if x.strip() and not x.strip().startswith("*")]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sufs = load_suffixes()

    dic = {}
    if os.path.exists(DICT):
        dic = json.load(open(DICT, encoding="utf-8"))
    have = {k.lower() for k in dic if not k.startswith("_")}

    todo = [s for s in sufs if s.strip().lower() not in have]
    print("OB Suffix 总数 %d，未翻译 %d" % (len(sufs), len(todo)))

    if cmd == "check":
        done = len(sufs) - len(todo)
        print("覆盖率 %.1f%%" % (done * 100.0 / len(sufs)))
        return 0

    if cmd == "review":
        bad = 0
        for s in todo:
            zh, unk = translate(s)
            if unk:
                bad += 1
                print("  %-46r -> %s   未识别: %s" % (s, zh, " ".join(unk)))
        print("\n有未识别 token 的: %d / %d" % (bad, len(todo)))
        return 0

    if cmd in ("gen", "apply"):
        result = {}
        unknown_all = {}
        for s in todo:
            zh, unk = translate(s)
            result[s] = zh
            if unk:
                unknown_all[s] = unk

        print("生成 %d 条" % len(result))
        if unknown_all:
            print("未识别 token 的条目 %d 条:" % len(unknown_all))
            for s, u in list(unknown_all.items())[:15]:
                print("   %-46r %s" % (s, " ".join(u)))

        if cmd == "gen":
            json.dump(result, open(OUT, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print("已写 %s（供审阅，未进词典）" % os.path.basename(OUT))
            return 0

        n = 0
        # 先写独立词条（做番号前缀用），只写 3 字符以上的，
        # 两字符的由 C 侧 lookup_short 处理。
        for k, v in TERMS.items():
            if len(k) >= 3 and k not in dic and k[0].isalpha():
                dic[k] = v
                n += 1
        for k, v in result.items():
            if k not in dic:
                dic[k] = v
                n += 1
        json.dump(dic, open(DICT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("已写入词典 %d 条，总计 %d"
              % (n, len([x for x in dic if not x.startswith("_")])))
        return 0

    print("未知命令:", cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
