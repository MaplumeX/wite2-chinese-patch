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


def is_split_index(tok, after):
    """拆分编号：斜杠前不带序数后缀的纯数字（"3/213th" 里的 3）。

    游戏把部队拆成 1/2/3 三个分遣队，这个数字是分遣队序号不是番号。
    但斜杠右边必须是"数字+序数后缀"，才说明右边是番号。
    反例 "1/West Wall"：右边是地名，1 是番号，要转成"第1西墙"。
    """
    if not (1 <= len(tok) <= 3) or not tok.isdigit():
        return False
    if not after.startswith("/"):
        return False
    return re.match(r"/\d+(?:th|st|nd|rd)", after) is not None


ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(t):
    """罗马数字转数值。从右往左，当前值小于右边值时相减。
    游戏里有非标准写法（XXXXVIII 表示 48），此算法同样正确。"""
    total = 0
    prev = 0
    for c in reversed(t):
        v = ROMAN_VAL.get(c)
        if v is None:
            return -1
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


def conv_num_prefix(t):
    """番号前缀，与 C 的 conv_num_prefix 逐行对应。

    刻意用和 C 一样的下标判断（i + 1 < len 而不是切片），这样边界
    写错时 Python 复刻也会失败 —— 之前用切片写，C 里的 i + 2 < len
    错误就测不出来。
    """
    n = len(t)
    if n == 0:
        return None
    # 罗马数字转阿拉伯并加"第"："XII Corps" -> 第12军
    if n >= 2 and all(c in "IVXLCDM" for c in t):
        v = roman_to_int(t)
        if 0 < v < 10000:
            return "第" + str(v)
        return t

    i = 0
    while i < n and t[i].isdigit():
        i += 1
    if i == 0:
        return None
    digits = t[:i]
    if i + 1 < n and t[i] == "s" and t[i + 1] == "t":
        i += 2
    elif i + 1 < n and t[i] == "n" and t[i + 1] == "d":
        i += 2
    elif i + 1 < n and t[i] == "r" and t[i + 1] == "d":
        i += 2
    elif i + 1 < n and t[i] == "t" and t[i + 1] == "h":
        i += 2
    if i != n:
        return None
    return "第" + digits


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
        if text[cut] == "/":
            pass
        elif text[cut] != " " or text[cut - 1] == " ":
            continue
        suf = text[cut + 1:tn]
        if len(suf) < 2:
            continue
        sl = suf.lower()
        hit = None
        via_prefix = False
        for k, v in entries:
            if len(k) == len(suf) and k.lower() == sl:
                hit = v
                break
        if hit is None:
            # 尾部被截断时做前缀回退。游戏有固定宽度控件，长名字会被
            # 砍掉末尾："18th Panzer Divisio"、"1/213th Sec Div"。
            # 砍掉后整串查不到就退回英文，英文更长又被继续砍。
            # 三条判据：尾串 >=6 字符、覆盖候选 >=55%、候选唯一。
            if len(suf) >= 6:
                # 第 4 条判据：截断点必须落在词中间。key[len(suf)] 是
                # 空格说明尾串是完整单词，不是被砍掉一半 —— "Armored"
                # 配到 "Armored Cars" 会得出 "第17装甲车"，那是误配。
                cands = [v for k, v in entries
                         if len(k) > len(suf)
                         and len(suf) * 100 >= len(k) * 55
                         and k[len(suf)] != " "
                         and k.lower().startswith(sl)]
                if len(cands) == 1:
                    hit = cands[0]
                    via_prefix = True
        if hit is None:
            continue
        head = text[lead:cut]
        mark = ""
        while head and head[0] in "-+—":
            mark += head[0]
            head = head[1:].lstrip(" ") if len(head) > 1 else ""
        if text[cut] == "/" and is_split_index(head, text[cut:]):
            # 分遣队序号，保留原样并把斜杠写出去
            pre = head + "/"
        else:
            pre = conv_num_prefix(head)
        if pre is None:
            parts = []
            nTok = nMiss = 0
            for mo in re.finditer(r"[^\s/]+|/", head):
                tok = mo.group()
                if tok == "/":
                    parts.append("/")
                    continue
                nxt = head[mo.end():]
                if is_split_index(tok, nxt):
                    parts.append(tok)
                    continue
                nTok += 1
                num = conv_num_prefix(tok)
                if num is not None:
                    parts.append(num)
                    continue
                # 不要用 tl：它保存整串小写，策略 4 还要用。
                # 这里曾经写成 tl = tok.lower()，把整串覆盖成最后一个
                # token，导致策略 4 永远匹配不上。
                tlw = tok.lower()
                got = None
                for k, v in entries:
                    if len(k) == len(tok) and k.lower() == tlw:
                        got = v
                        break
                if got is None and len(tok) == 2:
                    got = SHORT_PRE.get(tok.upper())
                if got is None:
                    nMiss += 1
                parts.append(got if got is not None else tok)
            # 只在后缀是截断匹配时才检查：后缀精确命中时番号部分
            # 查不到是正常的（"Andrews Field" 的 Andrews 是地名），
            # 拦掉会把已翻好的 "Andrews机场" 一起丢掉。
            if via_prefix and nTok > 0 and nMiss == nTok:
                continue
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
    ("XXIV Panzer Corps", "第24装甲军", "罗马数字转阿拉伯"),
    ("HQ Panzer Corps", "司令部装甲军", "两字符前缀走小表"),
    ("41 Tier 1a Panzer Division", "第41型1a装甲师", "多词前缀逐词翻"),
    ("German City Fortress", "德国城市要塞", "非数字前缀"),
    ("5th Guards Airborne Corps", "第5近卫空降军", "近卫部队"),
    ("Rum. Mountain Division", "罗马尼亚山地师", "国籍 + 兵种"),
    ("Foo Bar Baz", "Foo Bar Baz", "无法识别的不动"),
    # OOB 树形列表: 前导缩进 / 尾随空格 / 层级标记都必须原样保留
    (" XXIV Mot. Corps ", " 第24摩托化军 ", "前导缩进 + 尾随空格"),
    (" 255th Infantry Division ", " 第255步兵师 ", "缩进 + 番号"),
    ("  XII Corps ", "  第12军 ", "多空格缩进"),
    ("- 602nd Army SP Light Flak Battalion ",
     "-第602集团军属自行轻型高炮营 ", "层级标记 -"),
    # 罗马数字是部队正式番号，原样保留，不能换算成阿拉伯数字
    ("XXIV Mot. Corps", "第24摩托化军", "罗马数字转阿拉伯"),
    ("XII Corps", "第12军", "短罗马数字"),
    ("LI Corps", "第51军", "LI = 51"),
    ("XXXXVIII Panzer Corps", "第48装甲军", "非标准罗马写法"),
    # 逐词分支也要转数字，否则 "- 654th ..." 里的 654th 会原样保留
    ("- 654th Mot. Panzerjaeger Battalion",
     "-第654摩托化装甲猎兵营", "层级标记后的数字番号"),
    # 拆分编号：游戏把部队拆成 1/2/3 三个分遣队，斜杠左边是序号不是番号。
    # 判据是斜杠右边必须是"数字+序数后缀"，否则左边就是番号。
    ("3/213th Security Division", "3/第213警备师", "拆分编号保留"),
    ("2/6th Guards", "2/第6近卫", "拆分编号 + 词典命中的尾串"),
    ("1/998th Ost", "1/第998东方", "拆分编号 + 词典命中的尾串"),
    ("1/West Wall", "第1西墙", "斜杠右边非番号时左边是番号"),
    # 尾部截断：游戏固定宽度控件会砍掉长名字的末尾，砍完查不到词典
    # 就退回英文，英文更长又被继续砍。加前缀回退打破这个循环。
    ("18th Panzer Divisio", "第18装甲师", "尾部截断（缺 n）"),
    ("1/213th Sec Div", "1/第213警备师", "尾部截断 + 拆分编号"),
    ("1st Panzer Grou", "第1装甲集群", "尾部截断（缺 p）"),
    ("Panzer Divisio", "装甲师", "整串截断，走策略 4"),
    # 截断点必须落在词中间。"Armored" 是完整单词，配到 "Armored Cars"
    # 会得出 "第17装甲车"，那是误配，宁可保留英文。
    ("XVII Armored", "XVII Armored", "截断点不在词中间，不配"),
    # 后缀精确命中时，番号部分查不到是正常的（地名不翻），不能拦。
    # 这条曾经被"番号全未译就放弃切分"的判据误伤。
    ("Andrews Field", "Andrews机场", "地名 + 精确命中后缀"),
    ("255th Infantry Division", "第255步兵师", "阿拉伯数字照常转"),
    # 序数后缀 st/nd/rd/th 全部要能识别。C 里边界写成 i+2 < len 时
    # 只有 1st 侥幸通过，79th 会退回逐词翻译变成 "79th步兵师"。
    ("1st Panzer Division", "第1装甲师", "序数 st"),
    ("2nd Panzer Division", "第2装甲师", "序数 nd"),
    ("3rd Panzer Division", "第3装甲师", "序数 rd"),
    ("79th Infantry Division", "第79步兵师", "序数 th"),
    ("113th Infantry Division", "第113步兵师", "三位数 th"),
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
