#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 dat_trans/*.json 合并成运行时映射表 wite2_dat.tsv

格式：`原文<TAB>译文`（UTF-8）
  * 以 '#' 开头的行为注释
  * 以 '@' 开头的行需要反转义：\\t \\n \\\\

和 wite2_zh.tsv 用同一套转义约定，但这是**独立的表** —— 数据层匹配的是
整句精确相等，不做格式串回填、不做前后空格剥离。原因：`.dat` 里的串是
完整句子，任何"模糊"匹配都可能把 A 事件的正文替换成 B 事件的译文。

校验：
  * 译文不能比原文长（等长替换的硬约束）
  * 不能有重复原文
  * 不允许出现真实 TAB / 换行（转义后可以）
"""

import argparse
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRANS = os.path.join(ROOT, "dat_trans")
OUT = os.path.join(ROOT, "wite2_dat.tsv")


def esc(s):
    """需要转义吗？返回 (需要, 转义后的串)"""
    if "\t" in s or "\n" in s or "\r" in s or "\\" in s:
        return True, (s.replace("\\", "\\\\")
                       .replace("\t", "\\t")
                       .replace("\n", "\\n")
                       .replace("\r", "\\r"))
    return False, s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--check", action="store_true",
                    help="只校验不写文件")
    args = ap.parse_args()

    rows = []
    seen = {}
    problems = []

    for p in sorted(glob.glob(os.path.join(TRANS, "*.json"))):
        name = os.path.basename(p)[:-5]
        with io.open(p, encoding="utf-8") as f:
            m = json.load(f)
        for k, v in m.items():
            if k in seen:
                problems.append("重复原文（%s 与 %s）: %r"
                                % (seen[k], name, k[:60]))
                continue
            seen[k] = name
            if len(v) > len(k):
                problems.append(
                    "译文过长（%s）: %d > %d 字符  %r -> %r"
                    % (name, len(v), len(k), k[:40], v[:40]))
                continue
            if not v.strip():
                problems.append("空译文（%s）: %r" % (name, k[:40]))
                continue
            rows.append((k, v, name))

    print("合并 %d 条" % len(rows))
    by_file = {}
    for _, _, n in rows:
        by_file[n] = by_file.get(n, 0) + 1
    for n, c in sorted(by_file.items()):
        print("  %-24s %4d 条" % (n, c))

    if problems:
        print("\n✗ %d 个问题：" % len(problems))
        for x in problems:
            print("   " + x)
        return 1

    if args.check:
        print("\n校验通过（未写文件）")
        return 0

    # 哪些 .dat 有映射：运行时先比这个清单，不在清单里的文件直接放行，
    # 不做解密/解压。否则每次启动都要把 52 MB 全部解一遍。
    # dat_trans/*.json 是 w_gameEvent 的；事件正文实际来自剧本 .scn，
    # 所以再把所有 .scn 也纳入（同一段文本在多个剧本里重复出现）
    targets = sorted({n + ".dat" for n in by_file}) + ["*.scn"]

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 数据层翻译表（.dat 明文等长替换）\n")
        f.write("# 格式: 原文<TAB>译文   '@' 开头表示含转义 \\t \\n \\\\\n")
        f.write("#@files %s\n" % ",".join(targets))
        n_esc = 0
        for k, v, _ in rows:
            ke, k2 = esc(k)
            ve, v2 = esc(v)
            if ke or ve:
                n_esc += 1
                f.write("@%s\t%s\n" % (k2, v2))
            else:
                f.write("%s\t%s\n" % (k, v))

    sz = os.path.getsize(args.out)
    print("\n已写入 %s（%d 字节，转义行 %d）" % (args.out, sz, n_esc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
