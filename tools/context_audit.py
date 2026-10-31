# -*- coding: utf-8 -*-
"""把绘制现场变成"校对时能看见的上下文"。

先说结论
--------
控件级拿不到（ui_idx 是渲染批次，elem 结构为空），this 地址每次运行
都变 —— 所以**运行时按区域自动分译落不了地**。能落地的只有离线辅助：

  1. 邻居  这条文本同一行旁边是哪些词（表头？按钮？提示？）
  2. 预算  这个槽位到下一个文本有多宽，译文最多几个字
  3. 状态  最终到底显示中文还是英文（看 dst，不是 src）

注意 src 是传入的原文，dst 才是实际画出去的。看 src 会把 865 条
"英文原文 -> 中文" 误判成没翻译。

数据源: wite2_regions.tsv
    ret this this_vt ui_idx ui_cap txt_obj ctl ctl_vt x y style flags font text dst [elem]

用法:
    python tools/context_audit.py
    python tools/context_audit.py --csv build/context_audit.csv
"""

import argparse
import collections
import csv
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
DEFAULT_REGIONS = os.path.join(GAME, "wite2_regions.tsv")
DICT = os.path.join(ROOT, "zh_dict.json")

ESC = re.compile(r"<([0-9A-Fa-f]{4})>")
CJK = re.compile(r"[\u4e00-\u9fff]")

DYN = [
    re.compile(r"\d{1,2}\s*-\s*[A-Za-z]{3}\s*-\s*\d{4}"),
    re.compile(r"[A-Za-z]{3}\s+\d{1,2}\s+\d{4}"),
    re.compile(r"\bTurn\s+\d+", re.I),
    re.compile(r"\b\d+(st|nd|rd|th)\b", re.I),
    re.compile(r"\d+\s*/\s*\d+"),
    re.compile(r"\d+%"),
    re.compile(r"^\s*[-+]?\d[\d.,: ]*\s*$"),
    re.compile(r"^\s*[-–—?*#/\\|. ]+\s*$"),
]

UI_RULES = [
    ("主菜单", ["游戏选项", "偏好设置", "多人游戏", "选择剧本", "制作名单"]),
    ("游戏选项/偏好", ["战争迷雾", "战斗延迟", "恢复默认", "锁定司令部",
                       "两栖登陆", "空降", "撤退损耗"]),
    ("剧本选择", ["Campaign", "战役", "Scenario", "剧本"]),
    ("存档列表", ["载入存档", "保存", "回合"]),
    ("剧本历史/简介", ["invasion", "Soviet", "Eastern", "Front", "历史背景"]),
    ("部队详情", ["TOE", "Morale", "HHQ", "EXP", "RDY", "DAM", "FAT"]),
    ("航空部队", ["Air Group", "飞行队", "Pilots", "Mission", "Aircraft"]),
    ("编辑器", ["扩充代码", "胜利条件", "起始玩家", "地图西北", "不重建部队"]),
    ("制作名单", ["2BY3", "EXECUTIVE", "PRODUCER", "DESIGN", "PROGRAMMING"]),
    ("登录/账号", ["用户名", "密码", "隐私政策", "注册"]),
    ("对战记录", ["Wins :", "Losses:"]),
    ("地图顶栏", ["AP:", "T:", "Jun 22"]),
    ("警报", ["警报", "低补给", "被围", "未就绪", "损耗"]),
    ("地图格信息", ["Woods", "Clear", "河流", "天气", "水域", "积雪"]),
]


def unescape(s):
    return ESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def load_dict():
    d = json.load(io.open(DICT, encoding="utf-8"))
    keys = {k.lower(): k for k in d
            if not k.startswith("_") and isinstance(d[k], str)}
    return d, keys


def guess_ui(texts):
    best = (None, 0)
    for name, words in UI_RULES:
        n = sum(1 for w in words if any(w in t for t in texts))
        if n > best[1]:
            best = (name, n)
    return best[0]


def is_dynamic(t):
    return any(p.search(t) for p in DYN)


def load_rows(path):
    rows = []
    seen = set()
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            p = line.rstrip("\r\n").split("\t")
            if len(p) < 15:
                continue
            try:
                x, y = float(p[8]), float(p[9])
            except ValueError:
                continue
            if not (-100 <= x <= 2000 and -100 <= y <= 1200):
                continue
            src = unescape(p[13]).strip()
            dst = unescape(p[14]).strip()
            disp = dst or src
            if not disp:
                continue
            k = (p[1], round(x), round(y), disp)
            if k in seen:
                continue
            seen.add(k)
            rows.append({"this": p[1], "x": x, "y": y,
                         "src": src, "dst": dst, "disp": disp})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", default=DEFAULT_REGIONS)
    ap.add_argument("--csv", default=os.path.join(ROOT, "build",
                                                  "context_audit.csv"))
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not os.path.exists(args.regions):
        print("找不到 %s" % args.regions)
        return 2

    rows = load_rows(args.regions)
    dic, keys = load_dict()

    by_this = collections.defaultdict(list)
    for r in rows:
        by_this[r["this"]].append(r["disp"])
    panel = {th: (guess_ui(ts) or "未识别") for th, ts in by_this.items()}

    # 同一 y 行 = 界面上的同一行。邻居就是语义线索。
    lines = collections.defaultdict(list)
    for r in rows:
        lines[(r["this"], round(r["y"]))].append(r)
    for v in lines.values():
        v.sort(key=lambda r: r["x"])

    agg = collections.OrderedDict()
    stat = collections.Counter()
    for (th, y), items in lines.items():
        for i, r in enumerate(items):
            disp = r["disp"]
            width = ((items[i + 1]["x"] - r["x"]) if i + 1 < len(items)
                     else (1600 - r["x"]))
            budget = max(0, int(width / 17.3))   # 实测 S≈17.3 px/em

            if CJK.search(disp):
                state = "已译"
            elif disp.lower() in keys:
                state = "未替换"
            elif is_dynamic(disp):
                state = "动态"
            else:
                state = "未译"
            stat[state] += 1

            key = (disp, state)
            a = agg.setdefault(key, {
                "disp": disp, "state": state, "count": 0,
                "panels": set(), "neighbors": collections.Counter(),
                "budgets": [], "x": r["x"], "y": r["y"],
            })
            a["count"] += 1
            a["panels"].add(panel.get(th, "?"))
            for o in items:
                if o is not r:
                    a["neighbors"][o["disp"][:24]] += 1
            a["budgets"].append(budget)

    cols = ["状态", "最终显示", "英文", "当前译文", "出现次数", "面板",
            "同行邻居", "最小预算", "最大预算", "x", "y"]
    out = []
    for a in agg.values():
        en = keys.get(a["disp"].lower(), "")
        out.append({
            "状态": a["state"],
            "最终显示": a["disp"],
            "英文": en,
            "当前译文": dic.get(en, "") if en else "",
            "出现次数": a["count"],
            "面板": ", ".join(sorted(a["panels"]))[:80],
            "同行邻居": " / ".join(k for k, _ in a["neighbors"].most_common(4)),
            "最小预算": min(a["budgets"]),
            "最大预算": max(a["budgets"]),
            "x": int(round(a["x"])),
            "y": int(round(a["y"])),
        })
    order = {"未译": 0, "未替换": 1, "动态": 2, "已译": 3}
    out.sort(key=lambda r: (order[r["状态"]], -r["出现次数"]))

    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    with io.open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)

    print("绘制记录 %d 条（去重后），不同文本 %d 条，面板 %d 个"
          % (len(rows), len(agg), len(by_this)))
    print("状态: " + ", ".join("%s %d" % kv for kv in stat.most_common()))
    print("-> %s" % args.csv)

    print("\n未译 / 未替换（前 25 条，按出现次数）:")
    shown = 0
    for r in out:
        if r["状态"] not in ("未译", "未替换"):
            continue
        print("  %-4s %-34r 邻居: %-42s 预算 %d..%d"
              % (r["状态"], r["最终显示"][:34], r["同行邻居"][:42],
                 r["最小预算"], r["最大预算"]))
        shown += 1
        if shown >= 25:
            break

    # 超预算：译文比槽位宽。这才是真正的"过长"。
    over = [r for r in out if r["状态"] == "已译"
            and len(r["最终显示"]) > r["最大预算"] > 0]
    print("\n超出槽位预算的已译条目: %d" % len(over))
    for r in over[:15]:
        print("  %-30r 预算 %d 字，实际 %d 字"
              % (r["最终显示"][:30], r["最大预算"], len(r["最终显示"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
