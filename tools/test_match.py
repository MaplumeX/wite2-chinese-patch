#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟 dt_check 的三层匹配逻辑, 用于离线验证翻译效果。
不需要跑游戏就能发现"断章取义"问题。

用法:
    python tools/test_match.py           跑内置用例
    python tools/test_match.py --risky   列出所有有断章取义风险的词条
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DIVAB = {
    "ID": "步兵师", "MD": "摩托化师", "PD": "装甲师", "CD": "骑兵师",
    "SD": "警备师", "RD": "步兵师", "TD": "坦克师", "MGD": "机械化师",
    "ABD": "空降师", "GD": "近卫师",
}

ABBR = {
    "grp": "group", "div": "division", "bde": "brigade", "rgt": "regiment",
    "mot": "motorized", "inf": "infantry", "pz": "panzer", "cav": "cavalry",
    "art": "artillery", "hun": "hungarian", "rum": "rumanian", "mtd": "mounted",
    "bn": "battalion", "coy": "company", "sqd": "squadron",
    "mach": "machine", "mgun": "machinegun", "det": "detachment",
    "cdo": "commando", "para": "parachute", "abn": "airborne",
}


def load_entries():
    d = json.load(open(os.path.join(ROOT, "zh_dict.json"), encoding="utf-8"))
    out = []
    for k, v in d.items():
        if k.startswith("_") or v.startswith("----"):
            continue
        out.append((k, v))
    # 与 C 代码一致: 按 key 长度降序
    out.sort(key=lambda x: -len(x[0]))
    return out


def is_word_ch(c):
    return c.isascii() and (c.isalnum())


def lc_w(c):
    """与 C 代码一致的大小写折叠"""
    return c.lower() if c.isascii() else c


def norm_expand(s, state=None):
    out = []
    expanded = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isascii() and c.isalnum():
            w = []
            while i < n and s[i].isascii() and s[i].isalnum() and len(w) < 60:
                w.append(s[i].lower())
                i += 1
            word = "".join(w)
            if word in ABBR:
                expanded = True
            out.append(ABBR.get(word, word))
        else:
            i += 1
    if state is not None:
        state[0] = expanded
    return "".join(out)


SHORT_PRE = {
    "HQ": "司令部", "SP": "自行", "AA": "防空", "MG": "机枪",
    "LW": "空军", "SS": "党卫军", "FF": "自由法国", "BR": "英国",
    "US": "美国", "CA": "加拿大", "AU": "澳大利亚", "PO": "波兰",
    "NZ": "新西兰", "BE": "比利时", "IN": "印度", "GR": "希腊",
    "NE": "荷兰", "SA": "南非", "SU": "苏联", "RR": "铁路",
    "GS": "通用勤务", "KM": "海军",
}


def conv_num_prefix(t):
    """番号前缀: "113th" -> "第113", 罗马数字原样保留。返回 None 表示不是番号。"""
    if len(t) >= 2 and all(c in "IVXLCDM" for c in t):
        return t
    i = 0
    while i < len(t) and t[i].isdigit():
        i += 1
    if i == 0:
        return None
    for suf in ("st", "nd", "rd", "th"):
        if t[i:i+2] == suf:
            i += 2
            break
    if i != len(t):
        return None
    return "第" + t[:len(t)-i or None] if False else "第" + "".join(c for c in t if c.isdigit())


def try_numbered(text):
    m = re.fullmatch(r"(\d{1,4})(st|nd|rd|th) ([A-Z]{1,4})", text)
    if not m:
        return None
    ab = m.group(3)
    zh = DIVAB.get(ab)
    if not zh:
        return None
    return "第%s%s" % (m.group(1), zh)


CS_KEYS = {"Port","Rail","Road","City","Town","Airfield","Coast",
           "River","Water","Swamp","Forest","Woods","Mountain",
           "Urban","Desert","Tundra","Sand","Border","Clear","Rough",
           "Major","Minor","None","Snow","Mud"}


def dt_check(text, entries):
    """与 C 代码一致: 整串精确 -> 剥后缀 -> 数字模式 -> 截断前缀"""
    if not text:
        return text, None
    n = len(text)

    # 策略 1: 整串精确(大小写不敏感)
    tl = text.lower()
    for k, v in entries:
        if len(k) != n:
            continue
        if k in CS_KEYS:
            if k == text:
                return v, "exact-cs"
        elif k.lower() == tl:
            return v, "exact"

    # 策略 1.5: 剥离尾部空格后精确匹配
    # 界面用空格填充对齐，绘制出的串常带尾部空格：
    #   "1941 Campaign "   (剧本名从 .scn 文件名派生)
    # 不剥尾部空格会完全失配，表现为"有的翻了有的没翻"。
    tn = n
    while tn > 0 and text[tn - 1] == " ":
        tn -= 1
    if 0 < tn < n:
        prefix = text[:tn]
        pl = prefix.lower()
        for k, v in entries:
            if len(k) != tn:
                continue
            if k in CS_KEYS:
                if k == prefix:
                    return v + text[tn:], "trail-cs"
            elif k.lower() == pl:
                return v + text[tn:], "trail"

    # 策略 1.6: 剥离文件扩展名后精确匹配
    # "1941 Campaign.scn" -> "1941 Campaign"
    for ext in (".scn", ".dat", ".txt", ".csv", ".sav", ".psv", ".ini"):
        el = len(ext)
        if n <= el:
            continue
        if text[n - el:].lower() != ext:
            continue
        plen = n - el
        while plen > 0 and text[plen - 1] == " ":
            plen -= 1
        prefix = text[:plen]
        pl = prefix.lower()
        for k, v in entries:
            if len(k) != plen:
                continue
            if k in CS_KEYS:
                if k == prefix:
                    return v, "ext-cs"
            elif k.lower() == pl:
                return v, "ext"
        break

    # 策略 1.7: 尾部兵种短语匹配
    # OOB 列表名带前导缩进和尾随空格，都要原样保留
    lead = len(text) - len(text.lstrip(" "))
    lead = min(lead, 8)
    tn = n
    while tn > 0 and text[tn - 1] == " ":
        tn -= 1
    tail = text[tn:]
    for cut in range(lead + 1, tn - 1):
        if text[cut] != " " or text[cut - 1] == " ":
            continue
        suf = text[cut + 1:tn]
        if len(suf) < 3:
            continue
        sl = suf.lower()
        hit = None
        for k, v in entries:
            if len(k) == len(suf) and k.lower() == sl:
                hit = v
                break
        if hit is None:
            continue
        head = text[lead:cut]
        mark = ""
        while head and head[0] in "-+—":
            mark += head[0]
            head = head[1:].lstrip(" ") if len(head) > 1 else ""
        pre = conv_num_prefix(head)
        if pre is None:
            parts = []
            for tok in head.split():
                tl = tok.lower()
                got = None
                for k, v in entries:
                    if len(k) == len(tok) and k.lower() == tl:
                        got = v
                        break
                if got is None and len(tok) == 2:
                    got = SHORT_PRE.get(tok.upper())
                parts.append(got if got is not None else tok)
            pre = "".join(parts)
        return text[:lead] + mark + pre + hit + tail, "unit"

    # 策略 2: 剥离尾部后缀
    cut = -1
    for z in range(n - 1):
        if text[z] == " " and text[z + 1] in " (/.\n":
            cut = z
            break
    if cut >= 3:
        pl = text[:cut].lower()
        for k, v in entries:
            if len(k) == cut and k.lower() == pl:
                return v + text[cut:], "suffix"

    # 策略 3: 数字 + 兵种缩写
    r = try_numbered(text)
    if r:
        return r, "numbered"

    # 策略 3.5: 缩写展开(仅当确实展开了缩写)
    st = [False]
    nt = norm_expand(text, st)
    if st[0] and len(nt) >= 6:
        best = None
        best_len = 1 << 30
        for k, v in entries:
            if len(k) < len(nt) or len(k) >= best_len:
                continue
            if len(nt) * 2 < len(k):
                continue
            nk = norm_expand(k)
            if len(nk) < len(nt):
                continue
            if nt == nk[:len(nt)]:
                best = (k, v)
                best_len = len(k)
        if best:
            return best[1], "abbr"

    # 策略 4: 截断前缀(绘制文本是 key 的开头, 覆盖 >=70%, 截断点是词边界)
    for k, v in entries:
        kl = len(k)
        if kl <= n or n < 12:
            continue
        if n * 10 < kl * 7:
            continue
        if k.lower()[:n] == tl:
            return v, "trunc"

    return text, None


CASES = [
    # (输入, 期望输出, 说明)
    ("Starting player", "起始玩家", "必须整词匹配, 不能变成 开始ing player"),
    ("Start", "开始", "独立单词应正常翻译"),
    ("National", "National", "不能变成 国家al"),
    ("Nation", "国家", "独立单词应正常翻译"),
    ("Army Group Center", "中央集团军群", "完整名"),
    ("Army Grp. Sout", "南方集团军群", "缩写+截断"),
    ("1st Panzer Grou", "第1装甲集群", "截断"),
    ("113th ID", "第113步兵师", "数字+兵种缩写"),
    ("16th MD", "第16摩托化师", "数字+兵种缩写"),
    ("LI Corps (1/5)", "LI Corps (1/5)", "带括号后缀时整串不匹配就不动"),
    ("Army", "集团军", "独立单词, 精确匹配"),
    ("End", "结束", "独立单词"),
    ("Ending", "Ending", "不能变成 结束ing"),
    ("Supporting", "Supporting", "不能变成 支援ing"),
    ("Support", "支援", "独立单词"),
    ("Mapping", "Mapping", "不能变成 地图ping"),
    ("Map", "地图", "独立单词"),
    # 一词多义: 最长匹配优先
    ("Front Armor", "前装甲", "Front 在此处不是方面军"),
    ("Front", "方面军", "独立出现时是苏军编制"),
    # 单复数一致
    ("Air Group", "飞行队", "单数"),
    ("Air Groups", "飞行队", "复数必须和单数一致"),
    ("Air Base", "航空基地", "单数"),
    ("Air Bases", "航空基地", "复数必须和单数一致"),
    # 尾部空格 / 扩展名: 剧本名从 .scn 文件名派生，
    # 界面用空格填充对齐，绘制出的串常带尾部空格。
    ("1941 Campaign", "1941年战役", "整串精确"),
    ("1941 Campaign ", "1941年战役 ", "尾部空格要保留对齐"),
    ("1941 Campaign   ", "1941年战役   ", "多个尾部空格也要保留"),
    ("1941 Campaign.scn", "1941年战役", "剥离扩展名"),
    ("1941 Campaign .scn", "1941年战役", "扩展名前有空格"),
    ("foo.scn", "foo.scn", "词典里没有的不能乱改"),
    # 部队名: 番号 + 兵种短语拆开处理
    ("3rd Panzer Division", "第3装甲师", "阿拉伯数字番号"),
    ("XXIV Panzer Corps", "XXIV装甲军", "罗马数字番号原样保留"),
    ("HQ Panzer Corps", "司令部装甲军", "两字符前缀走小表"),
    ("41 Tier 1a Panzer Division", "41型1a装甲师", "多词前缀逐词翻"),
    ("German City Fortress", "德国城市要塞", "非数字前缀"),
    ("5th Guards Airborne Corps", "第5近卫空降军", "近卫部队"),
    ("Rum. Mountain Division", "罗马尼亚山地师", "国籍 + 兵种"),
    ("Foo Bar Baz", "Foo Bar Baz", "无法识别的不动"),
    # OOB 树形列表: 前导缩进 / 尾随空格 / 层级标记都必须原样保留
    (" XXIV Mot. Corps ", " XXIV摩托化军 ", "前导缩进 + 尾随空格"),
    (" 255th Infantry Division ", " 第255步兵师 ", "缩进 + 番号"),
    ("  XII Corps ", "  XII军 ", "多空格缩进"),
    ("- 602nd Army SP Light Flak Battalion ",
     "-第602集团军属自行轻型高炮营 ", "层级标记 -"),
    # 罗马数字是部队正式番号，原样保留，不能换算成阿拉伯数字
    ("XXIV Mot. Corps", "XXIV摩托化军", "罗马数字不转阿拉伯"),
    ("XII Corps", "XII军", "短罗马数字"),
    ("LI Corps", "LI军", "LI 是 51 但保留罗马"),
    ("255th Infantry Division", "第255步兵师", "阿拉伯数字照常转"),
]


def run_cases():
    entries = load_entries()
    print("词典 %d 条\n" % len(entries))
    ok = fail = 0
    for src, want, why in CASES:
        got, strat = dt_check(src, entries)
        good = (got == want)
        if good:
            ok += 1
            print("  OK   %-24s -> %-18s [%s]" % (src, got, strat))
        else:
            fail += 1
            print("  FAIL %-24s -> %-18s 期望 %-18s  (%s)"
                  % (src, got, want, why))
    print("\n%d 通过, %d 失败" % (ok, fail))
    return fail


def list_risky():
    """找出所有 key 是另一个 key 子串的情况: 这些有断章取义风险"""
    entries = load_entries()
    keys = [k for k, _ in entries]
    print("=== 有风险的词条 (key 是另一个 key 的子串) ===\n")
    n = 0
    for k in keys:
        if len(k) < 3:
            continue
        bigger = [o for o in keys if o != k and k in o
                  and (o.index(k) == 0 or not is_word_ch(o[o.index(k) - 1]))]
        if bigger:
            n += 1
            print("  %-26s 出现在: %s" % (k, ", ".join(bigger[:4])))
    if n == 0:
        print("  (无)")
    print("\n共 %d 条有风险 (已通过词边界检查缓解)" % n)

    print("\n=== 短词条 (长度 3-5, 建议确认是否独立使用) ===\n")
    for k, v in entries:
        if 3 <= len(k) <= 5:
            print("  %-12s -> %s" % (k, v))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--risky":
        return list_risky()
    return 1 if run_cases() else 0


if __name__ == "__main__":
    sys.exit(main())
