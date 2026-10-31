# -*- coding: utf-8 -*-
"""按调用点给捕获数据分组，反查每个字符串是哪个界面画的。

wite2_capture.txt 每行格式：
    返回地址@this <TAB> 原文 <TAB> 译文

同一个返回地址 = 同一处 draw 调用 = 同一个界面区域。把一组的
全部字符串摊开看，就能认出这是哪个面板（比如出现 "Unit Details"
就是部队详情）。

用法:
    python tools/callsite_report.py              # 全部调用点
    python tools/callsite_report.py --min 5      # 只看画了 5 条以上的
    python tools/callsite_report.py --ret 4A1234 # 只看一个调用点
"""

import argparse
import collections
import os
import re
import sys

GAME = (r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2")
CAP = os.path.join(GAME, "wite2_capture.txt")
ESC = re.compile(r"<([0-9A-Fa-f]{4})>")


def unescape(s):
    return ESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                ret_this, src, dst = parts
            elif len(parts) == 2:          # 旧格式
                ret_this, src, dst = "", parts[0], parts[1]
            else:
                continue
            ret, _, tail = ret_this.partition("@")
            this, _, chain = tail.partition(":")
            chain = chain  # 形如 00542A1C:0041B2C0:...
            rows.append((chain or ret, this, unescape(src), unescape(dst)))
    return rows


def looks_like_text(s):
    """过滤掉纯数字/坐标这类噪音，只留能当界面指纹的串。"""
    return bool(re.search(r"[A-Za-z]{3,}|[\u4e00-\u9fff]", s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=1, help="只看条数 >= N 的调用点")
    ap.add_argument("--ret", help="只看这个返回地址")
    ap.add_argument("--samples", type=int, default=8, help="每组显示几个样本")
    args = ap.parse_args()

    if not os.path.exists(CAP):
        print("找不到 %s" % CAP)
        print("游戏目录放空的 wite2_capture.ini，跑一次游戏。")
        return 2

    rows = load(CAP)
    if not rows:
        print("捕获文件是空的。")
        return 2

    groups = collections.defaultdict(list)
    for ret, this, src, dst in rows:
        groups[ret].append((src, dst, this))

    print("捕获 %d 条，%d 个调用点" % (len(rows), len(groups)))
    print()

    items = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    shown = 0
    for ret, items_ in items:
        if args.ret and not ret.upper().endswith(args.ret.upper()):
            continue
        if len(items_) < args.min:
            continue
        texts = [s for s, _, _ in items_ if looks_like_text(s)]
        if not texts:
            continue
        shown += 1
        thises = {t for _, _, t in items_}
        print("调用点 %s    %d 条   this=%s"
              % (ret or "?", len(items_), "/".join(sorted(thises))[:40]))
        # 样本挑长的看，短串多半是数字
        for s in sorted(set(texts), key=len, reverse=True)[:args.samples]:
            print("    %r" % s)
        print()

    if shown == 0:
        print("没有匹配的调用点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
