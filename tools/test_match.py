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


def norm_expand(s):
    out = []
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
            out.append(ABBR.get(word, word))
        else:
            i += 1
    return "".join(out)


def try_numbered(text):
    m = re.fullmatch(r"(\d{1,4})(st|nd|rd|th) ([A-Z]{1,4})", text)
    if not m:
        return None
    ab = m.group(3)
    zh = DIVAB.get(ab)
    if not zh:
        return None
    return "第%s%s" % (m.group(1), zh)


def dt_check(text, entries):
    """返回 (结果, 命中的策略)"""
    if not text:
        return text, None

    # 策略 1: 子串匹配(带词边界)
    out = []
    pos = 0
    changed = False
    first_strategy = None
    n = len(text)
    while pos < n:
        hit = None
        for k, v in entries:
            kl = len(k)
            if kl < 3 or pos + kl > n:
                continue
            if pos > 0 and is_word_ch(text[pos - 1]):
                continue
            if pos + kl < n and is_word_ch(text[pos + kl]):
                continue
            if text[pos:pos + kl] == k:
                hit = (k, v, kl)
                break
        if hit:
            out.append(hit[1])
            pos += hit[2]
            changed = True
            if first_strategy is None:
                first_strategy = "substr"
            continue
        out.append(text[pos])
        pos += 1

    if changed:
        return "".join(out), first_strategy

    # 策略 2: 数字 + 兵种缩写
    r = try_numbered(text)
    if r:
        return r, "numbered"

    # 策略 3: 缩写展开 + 前缀匹配
    nt = norm_expand(text)
    if len(nt) >= 6:
        for k, v in entries:
            if len(k) < 6:
                continue
            nk = norm_expand(k)
            if len(nk) < len(nt):
                continue
            if len(nt) * 2 < len(nk):
                continue
            if nt == nk[:len(nt)]:
                return v, "abbr"

    # 策略 4: 截断前缀
    for k, v in entries:
        kl = len(k)
        if kl <= n or n < 5 or n * 2 < kl:
            continue
        if text == k[:n]:
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
    ("LI Corps (1/5)", "第51军 (1/5)", "带后缀"),
    ("Army", "Army", "单字不应匹配到长 key"),
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
