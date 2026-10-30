#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自举词表法：用已确认的界面文本建立英文词汇表，再判断新串

原理：
  zh_dict.json 里 4800+ 条已翻译条目，它们的英文部分是**确认过的界面文本**。
  从这些条目里提取词汇，得到"界面英语词表"。
  任何新串如果由这些词组成（或包含它们），就极可能是界面文本。

  反之，Ax/Bz/Co/Hr/Id 这类缩写不会出现在界面英语词表里，自然被排除。

用法:
    python tools/vocab.py              统计 + 待翻清单
    python tools/vocab.py --todo       只输出待翻
    python tools/vocab.py --words      输出词表
"""

import json
import os
import re
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"

# 从 inventory.py 复用的排除项
sys.path.insert(0, HERE)
import inventory as INV  # noqa: E402


def load_strings():
    exe = os.path.join(GAME, "WarInTheEast2.exe")
    d = open(exe, "rb").read()
    e = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, e + 6)[0]
    optsz = struct.unpack_from("<H", d, e + 20)[0]
    secs = {}
    for i in range(nsec):
        o = e + 24 + optsz + 40 * i
        name = d[o:o + 8].rstrip(b"\0").decode()
        vs, va, rs, ra = struct.unpack_from("<IIII", d, o + 8)
        secs[name] = (va, vs, ra, rs)
    rd_va, _, rd_raw, rd_rs = secs[".rdata"]
    body = d[rd_raw:rd_raw + rd_rs]
    pat = re.compile(rb"(?:[\x20-\x7e]\x00){2,}")
    out, seen = [], set()
    for m in pat.finditer(body):
        s = m.group().decode("utf-16-le")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")


def build_vocab(dic):
    """从已确认的界面译文里提取英文词汇"""
    vocab = Counter()
    for k, v in dic.items():
        if k.startswith("_") or v.startswith("----"):
            continue
        # 只从多词条目取词（单词条目可能是缩写，不可靠）
        words = WORD_RE.findall(k)
        if len(words) >= 2:
            for w in words:
                vocab[w.lower()] += 1
    return vocab


def looks_like_ui(s, vocab, min_hits=1):
    """用词表判断是否像界面文本"""
    words = WORD_RE.findall(s)
    if not words:
        return False
    known = sum(1 for w in words if w.lower() in vocab)
    if known >= min_hits:
        return True
    # 单词串：必须是大写开头且长度>=4，且该词在词表里出现过
    if len(words) == 1:
        w = words[0]
        if w[0].isupper() and len(w) >= 4 and w.lower() in vocab:
            return True
    return False


def main():
    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)
    have = set(k.lower() for k in dic if not k.startswith("_"))
    vocab = build_vocab(dic)

    if "--words" in sys.argv:
        for w, c in vocab.most_common():
            print("%s\t%d" % (w, c))
        return 0

    strings = load_strings()
    seen_todo = set()

    # 迭代到收敛：每轮把新确认的界面串加入词表，再扫一遍。
    # 只跑一遍会漏掉"由新词组成"的串（例如 Armaments production
    # 需要先有 Armaments 和 production 都在词表里才被识别）。
    todo = []
    for round_no in range(6):
        found = []
        for s in strings:
            reason, ok = INV.classify(s)
            if not ok:
                continue
            if s.lower() in have:
                continue
            if s.lower() in seen_todo:
                continue
            if looks_like_ui(s, vocab):
                found.append(s)
        if not found:
            break
        for s in found:
            seen_todo.add(s.lower())
            for w in WORD_RE.findall(s):
                vocab[w.lower()] += 1
        todo.extend(found)

    todo.sort(key=lambda x: (len(x), x))

    if "--todo" in sys.argv:
        for s in todo:
            print(repr(s))
        return 0

    print("词表规模: %d 个词" % len(vocab))
    print("待翻译: %d 条\n" % len(todo))
    for s in todo[:250]:
        print(repr(s))
    if len(todo) > 250:
        print("... 还有 %d 条，用 --todo 输出全部" % (len(todo) - 250))
    return 0


if __name__ == "__main__":
    sys.exit(main())
