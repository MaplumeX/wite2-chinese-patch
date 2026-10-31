#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WITE2 翻译校对工具

用法:
    python tools/review.py export     导出校对表 review.csv (Excel 可直接打开)
    python tools/review.py import     把 review.csv 的修改导回 zh_dict.json
    python tools/review.py coverage   生成覆盖率报告 coverage.csv
    python tools/review.py todo       生成待翻译清单 todo_translate.csv
    python tools/review.py stats      只看统计

校对表列:
    分类   词条分组, 可自由改
    英文   原文 (不要改, 改了就对不上游戏)
    中文   译文 (在这里改)
    备注   你的批注, 不进游戏
    状态   自动算: OK / 过长 / 待译

关于"过长":
    绘制层是整体换指针, 文字不会被截断。但界面布局按原文长度排版,
    中文过长会视觉溢出或挤压相邻元素。
        "Show Production screen" 22 -> "显示生产界面" 6 字    OK
        "OKH" 3 -> "国防军陆军总司令部" 9 字                  过长, 建议 "陆总"
    经验值: 译文长度控制在原文的 1.5 倍以内比较安全。
"""

import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
REVIEW = os.path.join(ROOT, "review.csv")
COVER = os.path.join(ROOT, "coverage.csv")
TODO = os.path.join(ROOT, "todo_translate.csv")

GAME_DIRS = [
    r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2",
]

FIELDS = ["分类", "英文", "中文", "备注", "状态"]


def find_strings():
    for d in GAME_DIRS:
        p = os.path.join(d, "wite2_strings.txt")
        if os.path.exists(p):
            return p
    return None


def load_dict():
    with open(DICT, encoding="utf-8") as f:
        return json.load(f)


def save_dict(d):
    with open(DICT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def status_of(en, zh):
    if not zh or not zh.strip():
        return "待译"
    if zh.startswith("----"):
        return "分类标记"
    if len(zh) > len(en) * 1.5 and len(zh) > len(en) + 2:
        return "过长"
    return "OK"


def load_drawn():
    p = find_strings()
    if not p:
        return None
    raw = open(p, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    seen = set()
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def cmd_export():
    d = load_dict()
    rows = []
    cur = "未分类"
    for k, v in d.items():
        if k.startswith("_"):
            cur = k.lstrip("_")
            rows.append({"分类": cur, "英文": k, "中文": v,
                         "备注": "分类标记", "状态": "分类标记"})
            continue
        rows.append({"分类": cur, "英文": k, "中文": v,
                     "备注": "", "状态": status_of(k, v)})

    with open(REVIEW, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    cnt = {}
    for r in rows:
        cnt[r["状态"]] = cnt.get(r["状态"], 0) + 1
    print("导出 %d 条 -> %s" % (len(rows), REVIEW))
    for k, v in sorted(cnt.items()):
        print("   %-8s %d" % (k, v))


def cmd_import():
    if not os.path.exists(REVIEW):
        print("找不到 %s, 先跑 export" % REVIEW)
        return 1

    with open(REVIEW, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    old = load_dict()
    new = {}
    added = changed = skipped = 0

    for r in rows:
        en = (r.get("英文") or "").strip()
        zh = (r.get("中文") or "").strip()
        if not en:
            continue
        if zh.startswith("----"):
            new[en] = zh
            continue
        if not zh:
            skipped += 1
            continue
        if en not in old:
            added += 1
        elif old[en] != zh:
            changed += 1
        new[en] = zh

    for k, v in old.items():
        if k not in new:
            new[k] = v

    save_dict(new)
    print("导入完成: 新增 %d, 修改 %d, 空译文跳过 %d" % (added, changed, skipped))
    print("词典共 %d 条" % len(new))

    longs = [(k, v) for k, v in new.items()
             if not k.startswith("_") and v and status_of(k, v) == "过长"]
    if longs:
        print("\n提示: %d 条译文偏长, 界面上可能挤压相邻元素:" % len(longs))
        for k, v in longs[:20]:
            print("   %-26s %-22s (%d > %d)" % (k, v, len(v), len(k)))
    return 0


def cmd_coverage():
    drawn = load_drawn()
    if drawn is None:
        print("找不到 wite2_strings.txt")
        print("请在游戏目录放 wite2_collect.ini 并跑一次游戏")
        return 1

    d = load_dict()
    keys = [k for k in d if not k.startswith("_")]

    def covered(s):
        # 已含中文 = 已经翻译过了(游戏绘制的是我们的译文)
        if any("一" <= c <= "鿿" for c in s):
            return True
        for k in keys:
            if k in s:
                return True
        if re.match(r"^\d+(st|nd|rd|th)\s+[A-Z]{1,4}$", s):
            return True
        return False

    miss = [s for s in drawn if not covered(s)]
    hit = [s for s in drawn if covered(s)]

    with open(COVER, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["状态", "游戏实际绘制的文本", "长度"])
        for s in miss:
            w.writerow(["未翻译", s, len(s)])
        for s in hit:
            w.writerow(["已覆盖", s, len(s)])

    print("游戏绘制文本 %d 条" % len(drawn))
    print("  已覆盖 %d (%.0f%%)" % (len(hit), 100.0 * len(hit) / max(1, len(drawn))))
    print("  未翻译 %d" % len(miss))
    print("明细 -> %s" % COVER)
    return 0


def _kind(s):
    if re.fullmatch(r"[\d\s\.\-/:%;,%()=]+", s):
        return None
    if re.fullmatch(r"\d+(st|nd|rd|th)\s+[A-Z]{1,4}", s):
        return None
    if len(s) <= 2:
        return None
    if s.isupper() and len(s) > 2:
        return "界面-全大写"
    if s[0].isupper() and len(s.split()) <= 3:
        return "地名或专名"
    return "界面-其他"


def cmd_todo():
    drawn = load_drawn()
    if drawn is None:
        print("找不到 wite2_strings.txt, 先跑一次游戏收集")
        return 1

    d = load_dict()
    keys = [k for k in d if not k.startswith("_")]

    def covered(s):
        # 已含中文 = 已经翻译过了(游戏绘制的是我们的译文)
        if any("一" <= c <= "鿿" for c in s):
            return True
        for k in keys:
            if k in s:
                return True
        if re.match(r"^\d+(st|nd|rd|th)\s+[A-Z]{1,4}$", s):
            return True
        return False

    n = 0
    kinds = {}
    with open(TODO, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["分类", "原文", "译文", "备注"])
        for s in drawn:
            if covered(s):
                continue
            k = _kind(s)
            if k is None:
                continue
            w.writerow([k, s, "", ""])
            kinds[k] = kinds.get(k, 0) + 1
            n += 1

    print("待翻译 %d 条 -> %s" % (n, TODO))
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print("   %-12s %d" % (k, v))
    return 0


def cmd_stats():
    d = load_dict()
    keys = [k for k in d if not k.startswith("_")]
    st = {}
    for k in keys:
        s = status_of(k, d[k])
        st[s] = st.get(s, 0) + 1
    print("词典 %d 条" % len(keys))
    for k, v in sorted(st.items()):
        print("   %-8s %d" % (k, v))
    chars = {c for k in keys for c in d[k] if ord(c) > 0x7f}
    print("\n字体需覆盖 %d 个不同汉字" % len(chars))

    drawn = load_drawn()
    if drawn:
        print("游戏绘制文本 %d 条 (来自 wite2_strings.txt)" % len(drawn))
    return 0


CMDS = {
    "export": cmd_export,
    "import": cmd_import,
    "coverage": cmd_coverage,
    "todo": cmd_todo,
    "stats": cmd_stats,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        return 1
    return CMDS[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
