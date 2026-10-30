#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析全量捕获日志，找出没翻的绘制文本

用法：
    1. 游戏目录放一个空的 wite2_capture.ini
    2. 启动游戏，浏览要检查的界面（部队列表、OOB、详情面板、生产界面…）
    3. 退出游戏
    4. python tools/capture_report.py

为什么要这个：
    之前只能靠用户报告"哪个没翻"，然后按报告去猜字符串形态。XXXXVI
    Mot. Cr 修了三轮，因为每轮我建模的形态都和真实显示不一致。
    捕获日志给出的是游戏**实际绘制**的字符串，不用再猜。

输出：
    没翻的（原文还在），按出现顺序
    翻了的，用来确认覆盖面
"""

import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"


def unescape(s):
    """把 <XXXX> 还原成真字符"""
    return re.sub(r"<([0-9A-Fa-f]{4})>", lambda m: chr(int(m.group(1), 16)), s)


def load(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if "\t" not in line:
                continue
            src, dst = line.split("\t", 1)
            rows.append((unescape(src), unescape(dst)))
    return rows


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(GAME, "wite2_capture.txt")
    if not os.path.exists(path):
        print("找不到 %s" % path)
        print("先在游戏目录放一个空的 wite2_capture.ini，跑一次游戏。")
        return

    rows = load(path)
    print("捕获 %d 条绘制文本" % len(rows))

    # 判断"没翻"：译文里还有连续 3 个以上拉丁字母的词
    latin = re.compile(r"[A-Za-z]{3,}")
    roman = re.compile(r"\b[IVXLCDM]{2,}\b")

    missed = [(s, d) for s, d in rows if latin.search(roman.sub("", d) or d)]
    # 去掉纯数字/纯符号（本来就不该翻）
    missed = [(s, d) for s, d in missed
              if re.search(r"[A-Za-z]{3,}", re.sub(r"<[0-9A-F]{4}>", "", s))]

    print("其中 %d 条译文仍含英文：" % len(missed))
    print()
    seen = collections.Counter()
    for s, d in missed:
        seen[(s, d)] += 1
    for (s, d), n in seen.most_common():
        print("  %-52r -> %r" % (s, d))

    print()
    print("=== 已翻译样本（确认覆盖）===")
    done = [(s, d) for s, d in rows if not latin.search(d) and re.search(r"[A-Za-z]", s)]
    for s, d in done[:25]:
        print("  %-46r -> %r" % (s, d))


if __name__ == "__main__":
    main()
