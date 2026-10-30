#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 exe 的缩写表生成部队名缩写变体

游戏在窄控件里会把兵种名缩写，规则硬编码在 exe 里（偏移 0x5CEC14）：
    Mountain -> Mnt      Division -> Div      Panzer Division -> PzD
    Infantry -> Inf      Brigade  -> Bde      Motorized Division -> MD
一共 118 对。

所以同一个部队有两种显示形态：
    全称  4th Mountain Division
    缩写  4th Mnt Div

之前只翻了全称，缩写形态整片漏掉 —— 这是"手写了译文却还看到英文"的
真正原因。补法不是再手写 118 套缩写译文，而是**复用**：

    Mountain Division -> 山地师     （已手写）
    Mnt Div          -> 山地师     （本脚本生成）

译文完全来自已有条目，脚本只做键的形态变换，不产出任何新译文。
这是确定性映射，不是拼接翻译。

**只对 OBs.csv 的 Suffix 生成**。缩写规则只作用于部队编制名，
不能扩散到界面文本 —— 否则 "Road to Minsk"（剧本名）会被改成
"Rd to Minsk"，而游戏从不缩写剧本名。

用法:
    python tools/abbr_variants.py          # 预览
    python tools/abbr_variants.py --apply  # 写入 zh_dict.json
"""

import csv
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT = os.path.join(ROOT, "zh_dict.json")
ABBR = os.path.join(ROOT, "_abbr_table.json")
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
OBS = os.path.join(GAME, "Dat", "CSV", "Generic", "OBs.csv")


def load():
    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)
    with open(ABBR, encoding="utf-8") as f:
        table = json.load(f)
    return dic, table


def ob_suffixes():
    """OBs.csv 里所有 Suffix。缩写规则只对这些生效。"""
    raw = open(OBS, "rb").read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    out = set()
    for r in rows:
        s = (r.get("Suffix") or "").strip()
        if s and not s.startswith("*"):
            out.add(s)
    return out


def build_variants(dic, table, scope):
    """对作用域内的多词 key 生成缩写形态。

    两种变换：
      1. 整串映射：key 在缩写表里（"Panzer Division" -> "PzD"）
      2. 逐词映射：每个词在缩写表里（"Mountain Division" -> "Mnt Div"）

    只在目标形态不在词典里时生成，避免覆盖已有条目。
    """
    existing = {k for k in dic if not k.startswith("_")}
    out = {}

    for key in scope:
        if key not in dic:
            continue
        val = dic[key]
        # 整串映射
        if key in table:
            v = table[key]
            if v not in existing and v not in out:
                out[v] = (val, key)
        # 逐词映射
        words = key.split(" ")
        if len(words) > 1:
            mapped = [table.get(w, w) for w in words]
            v = " ".join(mapped)
            if v != key and v not in existing and v not in out:
                out[v] = (val, key)
    return out


def main():
    dic, table = load()
    scope = ob_suffixes()
    print("缩写表 %d 对，OB Suffix %d 条，词典 %d 条"
          % (len(table), len(scope), len([k for k in dic if not k.startswith("_")])))

    variants = build_variants(dic, table, scope)
    print("可生成缩写变体 %d 条：" % len(variants))
    for k, (v, src) in list(variants.items())[:30]:
        print("   %-30r %-16s  (来自 %r)" % (k, v, src))

    if "--apply" not in sys.argv:
        print("\n加 --apply 写入")
        return

    for k, (v, _src) in variants.items():
        dic[k] = v
    with open(DICT, "w", encoding="utf-8") as f:
        json.dump(dic, f, ensure_ascii=False, indent=2)
    print("\n写入 %d 条，词典现为 %d 条"
          % (len(variants), len([k for k in dic if not k.startswith("_")])))


if __name__ == "__main__":
    main()
