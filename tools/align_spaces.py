"""按像素宽度重算译文里的连续空格，修复表格列错位。

界面里很多串是拿空格对齐的：

    "    TURN        GAME"
    "Freight     Received    Used      To Units    Truck Use"

英文平均字符宽约 0.56 em，中文汉字是整 1.0 em。原样保留空格数量，
译文各列的起点就全变了 —— 表头和数据对不上，这是"汉化后没对齐"
最主要的原因。

做法：把原文和译文都按"连续空格"切成 token/空格交替序列，逐段把
译文空格数调整到"下一个 token 的起点宽度 == 原文该 token 的起点
宽度"。只处理两边 token 数量一致的条目，其余跳过（译文的词序或
结构变了，猜不准）。

用法:
    python tools/align_spaces.py            # 只预览
    python tools/align_spaces.py --apply    # 写回 zh_dict.json
"""

import json
import os
import re
import sys

from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
FONT = os.path.join(ROOT, "Dat", "Fonts", "wite2_zh.ttf")
SPACE = 606          # 字体里的空格 advance
UPM = 2048           # 汉字 advance


def load_adv():
    f = TTFont(FONT)
    hmtx = f["hmtx"]
    cmap = f.getBestCmap()

    def adv(ch):
        g = cmap.get(ord(ch))
        if g:
            return hmtx[g][0]
        return UPM if ord(ch) > 0x2000 else SPACE

    return adv


def w(s, adv):
    return sum(adv(c) for c in s)


def split_parts(s):
    """'    TURN   GAME' -> ['', '    ', 'TURN', '   ', 'GAME']"""
    return re.split(r"( +)", s)


def rebuild(src, dst, adv):
    """返回按原文列宽重算空格后的译文；结构不匹配返回 None。

    只处理真正的多列对齐（中间至少一个 2 空格以上的空格段、每个
    token 至少 2 字符、且译文每列都不比原文宽）。下面这些会被跳过：
      "Day / Night      "      token 是单字符，空格是分隔符不是对齐
      "[ INACTIVE ]  "        括号本身是 token，动的只是排版间距
      "     CBT   GHQ  ..."    译文比原文宽，压不回去
    """
    sp = split_parts(src)
    dp = split_parts(dst)
    if len(sp) != len(dp) or len(sp) < 3:
        return None
    toks = sp[0::2]
    gaps = sp[1::2]
    dtoks = dp[0::2]
    real = [t for t in toks if t]
    if len(real) < 2:                          # 只有一列，尾随空格不动
        return None
    if any(len(t) < 2 for t in real):
        return None
    if any(len(g) < 2 for g in gaps[:-1]):     # 尾随空格不参与判断
        return None
    if any(w(b, adv) > w(a, adv) for a, b in zip(toks, dtoks)):
        return None                            # 译文列更宽，空格救不了

    out = []
    src_pos = 0.0
    dst_pos = 0.0
    for i in range(len(sp)):
        if i % 2 == 1:                             # 空格段
            if i == len(sp) - 1:
                # 尾随空格：绘制时不计宽度，原样保留
                out.append(dp[i])
                src_pos += w(sp[i], adv)
                dst_pos += w(dp[i], adv)
            else:
                target = src_pos + w(sp[i], adv)   # 下一 token 的起点
                n = max(1, int(round((target - dst_pos) / SPACE)))
                out.append(" " * n)
                src_pos += w(sp[i], adv)
                dst_pos += n * SPACE
        else:                                      # token
            out.append(dp[i])
            src_pos += w(sp[i], adv)
            dst_pos += w(dp[i], adv)
    return "".join(out)


def main():
    apply = "--apply" in sys.argv
    adv = load_adv()
    with open(DICT, encoding="utf-8") as f:
        d = json.load(f)

    changed = []
    for k, v in list(d.items()):
        if k.startswith("_") or not isinstance(v, str) or v == "----":
            continue
        if "  " not in k or "  " not in v:
            continue
        nv = rebuild(k, v, adv)
        if nv is None or nv == v:
            continue
        changed.append((k, v, nv))
        d[k] = nv

    for k, old, new in changed:
        print("SRC %r" % k)
        print("  -  %r" % old)
        print("  +  %r" % new)
    print()
    print("共处理 %d 条" % len(changed))
    if not apply:
        print("（预览模式，加 --apply 写回）")
        return 0

    with open(DICT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("已写回 %s" % DICT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
