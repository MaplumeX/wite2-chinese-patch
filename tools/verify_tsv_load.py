#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回读 TSV，按 hook_text.c dict_load 的规则解析，核对词典条目是否全部落地

真实风险：TSV 是 TAB 分隔的纯文本，而词典里有两种 key 会破坏它 ——
以 '#' 开头的会被当注释行丢掉，含真实 TAB 的会把列分隔弄乱。
两者都改走 '@' 转义行。本脚本就是那个格式的验证器。

用法:
    python tools/verify_tsv_load.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

SCOPE = {"": 0, "r": 1, "d": 2, "b": 3, "t": 4}


def unescape_key(s):
    """与 C 的 dict_load 保持一致：\\\\t → TAB，\\\\n → LF，\\\\\\\\ → 反斜杠；
    其它 `\\\\X` 原样保留两个字符。"""
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
            out.append("\\")
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_tsv(path):
    loaded = {}
    stats = {"plain": 0, "escaped": 0, "comment": 0, "bad": 0}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line:
                continue
            if line[0] == "#":
                stats["comment"] += 1
                continue
            esc = line[0] == "@"
            body = line[1:] if esc else line
            if "\t" not in body:
                stats["bad"] += 1
                continue
            key, rest = body.split("\t", 1)
            cols = rest.split("\t")
            if len(cols) > 2:
                stats["bad"] += 1
                continue
            val = cols[0]
            scope = cols[1] if len(cols) == 2 else ""
            if scope not in SCOPE:
                stats["bad"] += 1
                continue
            if esc:
                key = unescape_key(key)
                stats["escaped"] += 1
            else:
                stats["plain"] += 1
            loaded[key] = val
    return loaded, stats


def main():
    if not os.path.exists(TSV):
        sys.exit("找不到 %s" % TSV)
    dic = json.load(open(DICT, encoding="utf-8"))
    keys = [k for k in dic if not k.startswith("_")]
    loaded, stats = load_tsv(TSV)

    print("TSV 行统计: 普通 %d / 转义 %d / 注释 %d / 异常 %d"
          % (stats["plain"], stats["escaped"],
             stats["comment"], stats["bad"]))
    print("词典正文 %d 条，TSV 解析出 %d 条" % (len(keys), len(loaded)))

    missing = [k for k in keys if k not in loaded]
    print("未落地 %d 条" % len(missing))
    for k in missing[:10]:
        print("   %r" % k[:70])

    for label, sel in (
        ("以 # 开头", [k for k in keys if k.startswith("#")]),
        ("含真实制表符", [k for k in keys if "\t" in k]),
        ("以 @ 开头", [k for k in keys if k.startswith("@")]),
    ):
        if not sel:
            continue
        ok = sum(1 for k in sel if k in loaded)
        print("  %s: %d / %d 条落地%s"
              % (label, ok, len(sel), "" if ok == len(sel) else "  ← 有问题"))

    # 译文不该带着列标记漏出来
    bad = [k for k, v in loaded.items()
           if isinstance(v, str) and v.endswith(("\td", "\tr", "\tb", "\tt"))]
    if bad:
        print("  !! %d 条译文带列标记（界面会显示「大雨d」这种）" % len(bad))
        for k in bad[:5]:
            print("     %r -> %r" % (k[:40], loaded[k][-12:]))

    ok_all = not missing and not bad and stats["bad"] == 0
    print("\n%s" % ("全部通过" if ok_all else "有问题，见上"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
