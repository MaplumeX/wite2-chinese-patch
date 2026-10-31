#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 dat_trans/*.json 合并成运行时映射表 wite2_dat.tsv

格式：`原文<TAB>译文`（UTF-8）
  * 以 '#' 开头的行为注释
  * 以 '@' 开头的行需要反转义：\\t \\n \\\\

和 wite2_zh.tsv 用同一套转义约定，但这是**独立的表** —— 数据层匹配的是
整句精确相等，不做格式串回填、不做前后空格剥离。原因：`.dat` / `.scn`
里的串是完整句子，任何"模糊"匹配都可能把 A 事件的正文替换成 B 的译文。

中文必须自己换行
----------------
游戏是按**空格分词**来折行的。英文原文靠这个自动换行，中文没有空格，
一整个段落会变成"一个词"，于是不折行、直接冲出显示区域。

所以这里在生成阶段就把译文按行宽预先插 `<cr>` 硬断行：

    <cr>1941年6月22日，苏联入侵行动开始，东线就此开辟……<cr>入侵开始时，……

`<cr>` 是游戏自己的换行标记（原文用它分隔段落），所以这是"用游戏的
机制说游戏的话"，不依赖任何渲染行为。

行宽用 **东亚宽度** 计（中文 2、ASCII 1），这样同一个 `--wrap` 值对
中英混排都适用。默认 72（≈36 个汉字），可用 --wrap 调整。

等长约束：插 `<cr>` 会增加字符数，必须保证总长仍不超过原文。
实测第一条 1526 字符原文、380 字译文，加 11 个 `<cr>` 后 424 字符，
离上限还很远。超了会直接报错，不会静默截断。
"""

import argparse
import glob
import io
import json
import os
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRANS = os.path.join(ROOT, "dat_trans")
OUT = os.path.join(ROOT, "wite2_dat.tsv")

# 折行断点优先级：句末 > 句内停顿 > 空格
# 一定要**在标点之后**断，否则下一行会以「，」开头，很难看
BREAK_SENTENCE = "。！？"
BREAK_CLAUSE = "，、；："
BREAK_SPACE = " "


def eaw(ch):
    """东亚宽度：全角算 2，半角算 1"""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def has_cjk(s):
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _pick_cut(cur, width):
    """在 cur 里挑一个断点位置（返回切在几个字符之后）。

    规则：从最靠后的位置往前找，先找标点，再找空格。
    找不到任何断点才硬切 —— 硬切虽然不好看，但至少不会溢出。
    """
    n = len(cur)
    # 只要断在 40% 之后的标点就算合理，太靠前会把行切得太短
    floor = max(1, int(n * 0.4))
    for chars in (BREAK_SENTENCE, BREAK_CLAUSE, BREAK_SPACE):
        for k in range(n - 1, floor - 1, -1):
            if cur[k] in chars:
                return k + 1
    return n          # 硬切


def wrap_segment(seg, width):
    """把一段（不含 <cr>）按东亚宽度折行，返回若干行"""
    if not seg:
        return [seg]
    lines = []
    rest = seg
    while rest:
        cur = []
        curw = 0
        for ch in rest:
            w = eaw(ch)
            if cur and curw + w > width:
                break
            cur.append(ch)
            curw += w
        if len(cur) >= len(rest):
            lines.append("".join(cur))
            break
        cut = _pick_cut(cur, width)
        lines.append("".join(cur[:cut]))
        rest = "".join(cur[cut:]) + rest[len(cur):]
    return lines


def wrap_text(text, width):
    """对已经含 <cr> 的文本逐段折行，保留原有 <cr>"""
    parts = text.split("<cr>")
    out = []
    for p in parts:
        if not has_cjk(p):
            out.append(p)          # 纯英文/数字段不动
            continue
        out.append("<cr>".join(wrap_segment(p, width)))
    return "<cr>".join(out)


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
    ap.add_argument("--wrap", type=int, default=72,
                    help="折行宽度（东亚宽度单位，中文算2；0=不折行）")
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

            if not v.strip():
                problems.append("空译文（%s）: %r" % (name, k[:40]))
                continue

            v2 = wrap_text(v, args.wrap) if args.wrap > 0 else v
            if len(v2) > len(k):
                problems.append(
                    "译文过长（%s）: 折行后 %d > 原文 %d 字符  %r"
                    % (name, len(v2), len(k), k[:40]))
                continue
            rows.append((k, v2, name, len(v2) - len(v)))

    print("合并 %d 条（--wrap %d）" % (len(rows), args.wrap))
    by_file = {}
    for _, _, n, _ in rows:
        by_file[n] = by_file.get(n, 0) + 1
    for n, c in sorted(by_file.items()):
        print("  %-24s %4d 条" % (n, c))
    for k, v, n, grew in rows:
        if grew:
            print("  折行: 原文 %d 字符 -> 译文 %d 字符（插入 <cr> 增加 %d）"
                  % (len(k), len(v), grew))

    if problems:
        print("\n✗ %d 个问题：" % len(problems))
        for x in problems:
            print("   " + x)
        return 1

    if args.check:
        print("\n校验通过（未写文件）")
        return 0

    # 哪些文件有映射：运行时先比这个清单，不在清单里的文件直接放行，
    # 不做解密/解压。否则每次启动都要把 52 MB 全部解一遍。
    # dat_trans/*.json 是 w_gameEvent 的；事件正文实际来自剧本 .scn，
    # 所以再把所有 .scn 也纳入（同一段文本在多个剧本里重复出现）
    targets = sorted({n + ".dat" for n in by_file}) + ["*.scn"]

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 数据层翻译表（.dat/.scn 明文等长替换）\n")
        f.write("# 格式: 原文<TAB>译文   '@' 开头表示含转义 \\t \\n \\\\\n")
        f.write("#@files %s\n" % ",".join(targets))
        n_esc = 0
        for k, v, _, _ in rows:
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
