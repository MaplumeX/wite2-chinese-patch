# -*- coding: utf-8 -*-
"""按绘制现场把捕获到的文本分组，回答"这句话是哪个界面画的"。

为什么需要
----------
游戏只有一个文本绘制入口 0x45CE60，所有界面都走它。而 .rdata 里的
UI 字符串**没有任何静态指针引用**（全 exe 实测 0 处，连 .reloc 里也
没有），所以无法靠"哪个函数引用了这句话"反推界面。

能用的只有绘制现场。wite2_capture.txt 每行:

    返回地址@this:调用链 <TAB> 原文 <TAB> 译文

  - this   绘制时的大对象指针（偏移到 0x1200d4，约 1.15 MB）
  - chain  入口 esp 往上扫到的 .text 地址链

这两个字段的可靠性不一样，报告里分开看:

  this   稳定，同一次运行里一个界面/文档实例一个。可作为粗粒度区域键。
  chain  会混入假阳性 —— 0xC0C0C0 / 0xCDCDCD 这类栈填充值，以及恰好
         落在 .text 里的函数指针参数。给了 --dump 时，会用"返回地址前
         5 字节是不是 E8 call"做一次校验，把明显不对的帧标出来。

产出
----
  - 每个区域（this + 链前缀）的文本集合，能直接认出是哪个面板
  - 跨区域重复出现的文本 -> 上下文歧义候选，这些正是需要按界面分译的
  - 绘制顺序里 this 的切换点 -> 这次运行经过了哪些界面

用法
----
    python tools/region_report.py
    python tools/region_report.py --in tests/capture_2024-10.txt
    python tools/region_report.py --dump "G:/.../wite2_text.bin" --md build/regions.md

两种数据源
----------
1. `wite2_regions.tsv`（新版采集器产出，推荐）

   列: ret this this_vt ui_idx ui_cap txt_obj ctl ctl_vt x y style flags font text dst

   其中 ctl_vt 是控件对象的 vtable。控件管理器的寻址是从 0x445020 反
   汇编出来的（见 hook_text.c 的“区域采集”注释），vtable 是精确地址
   匹配，比栈链可靠得多。

2. `wite2_capture.txt`（旧版，只有 3 帧栈链）

   实测撑不起区域划分: 最粗的一条链独占 186/380 条，横跨 6 个界面。
   保留这个模式只是为了能回看历史数据。
"""

import argparse
import collections
import io
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
DEFAULT_IN = os.path.join(GAME, "wite2_capture.txt")
DEFAULT_REGIONS = os.path.join(GAME, "wite2_regions.tsv")
FALLBACK_IN = os.path.join(ROOT, "tests", "capture_2024-10.txt")
DICT = os.path.join(ROOT, "zh_dict.json")

ESC = re.compile(r"<([0-9A-Fa-f]{4})>")

# 栈填充值。扫栈时这些值经常落在 .text 范围内，但不是返回地址。
FILLERS = {
    0xC0C0C0C0, 0xC0C0C0, 0xCDCDCDCD, 0xFEEEFEEE, 0xABABABAB,
    0xDDDDDDDD, 0xBAADF00D, 0xFFFFFFFF, 0x00000000,
}

# 关键词 -> 界面候选。命中数只作为线索，不硬贴标签：
# 同一个链可能横跨多个面板，命名交给人确认。
RULES = [
    ("主菜单", ["游戏选项", "偏好设置", "多人游戏", "制作名单", "载入存档",
                "Game Options", "Preferences", "Multiplayer", "Credits"]),
    ("剧本选择", ["Campaign", "战役", "Scenario", "剧本", "Velikie Luki",
                  "Typhoon", "Karelia", "Stalingrad", "Berlin"]),
    ("存档/载入列表", ["载入存档", "保存", "Load", "Save", "回合"]),
    ("部队详情", ["TOE", "Morale", "HHQ", "编制", "Nation", "Ready",
                  "Damaged", "Fatigue", "补给", "支援"]),
    ("航空部队", ["Air Group", "飞行队", "Pilots", "飞行员", "Aircraft",
                  "飞机", "Mission", "任务", "Rest", "休整"]),
    ("地图/格信息", ["Woods", "Clear", "河流", "天气", "地形", "Hex",
                     "水", "雪", "移动点"]),
    ("数据文件/导出", ["units", "planes", "pilots", "locations", "aircraft",
                       "toe(ob)", "sevices"]),
    ("编辑器", ["Editor", "编辑器", "AI 脚本", "AI Script", "地图"]),
    ("生产/后勤", ["Factory", "工厂", "Pool", "储备", "Repair", "修",
                   "Production", "生产"]),
]

REPL = re.compile(
    r"^(\w+)@(\w+):(\w+(?::\w+)*)\t(.*?)\t(.*)$")


def unescape(s):
    return ESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def load_rev_dict():
    """中文 -> 英文（取最短的 key，通常就是原文而不是某个长句）。"""
    rev = {}
    try:
        with io.open(DICT, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return rev
    for k, v in d.items():
        if k.startswith("_") or not isinstance(v, str) or v == "----":
            continue
        cur = rev.get(v)
        if cur is None or len(k) < len(cur):
            rev[v] = k
    return rev


def load_capture(path):
    rows = []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) == 3:
                head, src, dst = parts
            elif len(parts) == 2:
                head, src, dst = "", parts[0], parts[1]
            else:
                continue
            ret, _, tail = head.partition("@")
            this, _, chain = tail.partition(":")
            rows.append({
                "line": lineno,
                "ret": ret.upper(),
                "this": this.upper(),
                "chain": [c.upper() for c in chain.split(":") if c],
                "src": unescape(src),
                "dst": unescape(dst),
            })
    return rows


class Checker(object):
    """给了 .text dump 时，校验每个链帧像不像真的返回地址。"""

    def __init__(self, dump_path, base):
        self.text = None
        self.base = base
        if dump_path and os.path.exists(dump_path):
            with open(dump_path, "rb") as f:
                self.text = f.read()

    def verdict(self, addr):
        """返回 '+'(像 call 返回) / '?'(落在 .text 但前一条不是 E8)。

        间接调用（call [eax+0x2c]）也是合法返回地址，所以 '?' 不等于
        假阳性，只表示"这个校验方法判不了"。
        """
        if self.text is None:
            return " "
        off = addr - self.base - 0x1000
        if not (5 <= off < len(self.text)):
            return " "
        return "+" if self.text[off - 5] == 0xE8 else "?"


def looks_like_text(s):
    return bool(re.search(r"[A-Za-z]{2,}|[\u4e00-\u9fff]", s))


def classify(texts):
    """返回 (候选界面名, 命中的关键词)。只做线索，不做断言。"""
    best = (None, [], 0)
    for name, words in RULES:
        hits = [w for w in words if any(w in t for t in texts)]
        if len(hits) > best[2]:
            best = (name, hits, len(hits))
    return best[0], best[1]


UI_RULES = [
    ("主菜单", ["游戏选项", "偏好设置", "多人游戏", "选择剧本", "制作名单",
                "载入存档", "Game Options"]),
    ("游戏选项/偏好", ["战争迷雾", "战斗延迟", "恢复默认", "锁定司令部",
                       "两栖登陆", "空降", "撤退损耗", "非战斗减员",
                       "移动战争迷雾", "自动AI"]),
    ("剧本选择", ["Campaign", "战役", "Scenario", "剧本", "Velikie",
                  "Typhoon", "Karelia"]),
    ("存档列表", ["载入存档", "保存", "回合", "Load", "Save"]),
    ("剧本历史/简介", ["invasion", "Soviet", "Eastern", "Front",
                       "闪击战", "莫斯科", "历史背景"]),
    ("部队详情", ["TOE", "Morale", "HHQ", "EXP", "RDY", "DAM", "FAT",
                  "Army Group", "补给中", "已分配"]),
    ("航空部队", ["Air Group", "飞行队", "Pilots", "Mission", "Rest",
                  "Aircraft", "飞机"]),
    ("编辑器", ["扩充代码", "胜利条件", "起始玩家", "地图西北", "不重建部队",
                "编辑器", "ADMIN"]),
    ("制作名单", ["2BY3", "EXECUTIVE", "PRODUCER", "DESIGN", "PROGRAMMING",
                  "SCENARIO DESIGN"]),
    ("登录/账号", ["用户名", "密码", "隐私政策", "注册", "Account"]),
    ("对战记录", ["Wins :", "Losses:"]),
    ("地图顶栏", ["AP:", "T:", "Jun 22", "Jun 22 1941"]),
    ("警报", ["警报", "低补给", "被围", "未就绪", "损耗"]),
    ("地图格信息", ["Woods", "Clear", "河流", "天气", "水域", "积雪"]),
]


def guess_ui(texts):
    """按关键词给区域一个候选名。返回 [(名字, 命中词), ...]，最多两个。

    只做线索，不硬贴标签 —— 同一个区域往往混着多个面板的文本。
    """
    scored = []
    for name, words in UI_RULES:
        hits = [w for w in words if any(w in t for t in texts)]
        if hits:
            scored.append((name, hits))
    scored.sort(key=lambda x: -len(x[1]))
    return scored[:2]


def report_regions(path, args, rev):
    """新版: wite2_regions.tsv。

    分组键优先 ctl_vt（控件 vtable），读不到就退回 this。
    实测 this 是"面板/视图"级：编辑器一个标签页一个 this，主菜单一个。
    同一个 this 里还会混着地图世界坐标，所以默认只留屏幕坐标内的文本。
    """
    rows = []
    seen = set()
    dup = 0
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip() or line.startswith("#"):
                continue
            p = line.rstrip("\r\n").split("\t")
            if len(p) < 15:
                continue
            try:
                r = {
                    "ret": p[0], "this": p[1], "this_vt": p[2],
                    "ui_idx": int(p[3]), "ui_cap": int(p[4]),
                    "txt_obj": p[5], "ctl": p[6], "ctl_vt": p[7],
                    "x": float(p[8]), "y": float(p[9]),
                    "style": int(p[10]), "flags": int(p[11]),
                    "font": p[12], "text": unescape(p[13]),
                    "dst": unescape(p[14]),
                    "line": lineno,
                }
            except ValueError:
                continue
            # 游戏每帧重画同一批文本。不去重的话 20 万行上限几秒就打满。
            key = (r["this"], r["font"], round(r["x"]), round(r["y"]),
                   r["text"])
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            rows.append(r)
    if not rows:
        print("regions 文件里没有可解析的行: %s" % path)
        return None, None

    ui_only = getattr(args, "ui_only", True)
    total = len(rows)
    if ui_only:
        rows = [r for r in rows
                if -100 <= r["x"] <= 2000 and -100 <= r["y"] <= 1200]

    def show(s):
        en = rev.get(s)
        return "%s [%s]" % (s, en) if en and en != s else s

    use_ctl = any(r["ctl_vt"] not in ("00000000", "0", "") for r in rows)
    keyname = "ctl_vt" if use_ctl else "this"

    by = collections.OrderedDict()
    for r in rows:
        by.setdefault(r[keyname], []).append(r)

    text_key = collections.defaultdict(set)
    for r in rows:
        if r["text"].strip():
            text_key[r["text"]].add(r[keyname])
    ambiguous = {t: k for t, k in text_key.items() if len(k) > 1}

    out = []
    w = out.append
    w("# 控件区域报告\n")
    w("数据源: `%s`  " % path)
    w("原始 %d 行 → 按 `(this, font, x, y, text)` 去重后 %d 条"
      "（去掉 %d 条重复绘制）→ 只留屏幕坐标后 **%d** 条  "
      % (total + dup, total, dup, len(rows)))
    w("分组键: `%s`%s  \n"
      % (keyname,
         "（控件 vtable）" if use_ctl else
         "（this = 面板/视图；控件数组没读到，见文末）"))
    w("> 同一个 this 里既有界面文本、又有地图世界坐标（y 是 -4000 那种）。"
      "默认按 `-100..2000 / -100..1200` 过滤屏幕层；加 `--all` 看全部。\n")

    for k, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        texts = list(dict.fromkeys(
            r["text"] for r in rs if r["text"].strip()))
        cands = guess_ui(texts)
        fonts = collections.Counter(r["font"] for r in rs)
        xs = [r["x"] for r in rs]
        ys = [r["y"] for r in rs]
        w("\n## %s=%s   %d 条，%d 个不同文本"
          % (keyname, k, len(rs), len(texts)))
        if cands:
            w("- 候选: **%s**%s"
              % (cands[0][0],
                 "（备选 %s）" % cands[1][0] if len(cands) > 1 else ""))
            w("- 命中词: %s" % "、".join(cands[0][1][:8]))
        w("- 坐标: x %.0f..%.0f  y %.0f..%.0f"
          % (min(xs), max(xs), min(ys), max(ys)))
        w("- 字体: %s" % ", ".join("%s×%d" % kv
                                   for kv in fonts.most_common(3)))

        # 同一 x 的一列文本 = 同一种控件（标签列/值列/列表列）。
        cols = collections.Counter(round(r["x"]) for r in rs)
        w("- 列（同一个 x 的文本归为一列，最多 5 列）:")
        for x, c in cols.most_common(5):
            ct = list(dict.fromkeys(
                r["text"].strip() for r in rs
                if round(r["x"]) == x and r["text"].strip()))
            w("  - x=%-5d %3d 条，%2d 种: %s"
              % (x, c, len(ct), " | ".join(ct[:6])[:140]))

        # 同一 y 上的一组文本 = 界面上的一行。按 y 排出来就是布局。
        lines = collections.OrderedDict()
        for r in rs:
            lines.setdefault(round(r["y"]), []).append(r)
        w("- 布局（按 y 行，最多 10 行）:")
        for y in sorted(lines)[:10]:
            items = sorted(lines[y], key=lambda r: r["x"])
            cell = " / ".join("%.0f:%s" % (r["x"], r["text"].strip()[:20])
                              for r in items[:6])
            w("  - y=%-6d %s" % (y, cell))

        w("- 文本样本:")
        for s in sorted(texts, key=len, reverse=True)[:args.samples]:
            w("  - %s" % show(s))

    w("\n## 跨面板重复文本（需要按位置分译的候选）\n")
    if ambiguous:
        w("| 文本 | 英文 | 面板数 | %s |" % keyname)
        w("|---|---|---|---|")
        for t, ks in sorted(ambiguous.items(),
                            key=lambda kv: -len(kv[1]))[:100]:
            w("| %s | %s | %d | %s |"
              % (t, rev.get(t, ""), len(ks),
                 ", ".join(sorted(ks)[:4])))
    else:
        w("没有跨面板重复的文本。")

    if not use_ctl:
        w("\n## 文末：控件数组为什么没读到\n")
        w("`ui_idx` / `ui_cap` / `ctl` 全是 0，说明 `0x1272800` 的字段偏移")
        w("（`+0x200/+0x218/+0x21C`）在这个版本上不成立，或者绘制时刻索引")
        w("已经被清空。本次改用 `this` 兜底。")
        w("`[this]` 也不是 vtable（读出来是 0x00490057，宽字符 \"WI\"），")
        w("说明这个对象头部是内嵌字符串，不是多态类。")
        w("下一版采集器先打印 `[ui]` 的实际内容，再决定偏移。")

    md = "\n".join(out) + "\n"
    data = {
        "source": path,
        "total": total,
        "deduped_from": total + dup,
        "ui_rows": len(rows),
        "group_key": keyname,
        "groups": {k: {
            "count": len(rs),
            "guess": [c[0] for c in guess_ui(
                [r["text"] for r in rs if r["text"].strip()])],
            "texts": list(dict.fromkeys(
                r["text"] for r in rs if r["text"].strip())),
            "cols": {str(x): list(dict.fromkeys(
                r["text"].strip() for r in rs
                if round(r["x"]) == x and r["text"].strip()))
                for x in sorted({round(r["x"]) for r in rs})},
            "x": [min(r["x"] for r in rs), max(r["x"] for r in rs)],
            "y": [min(r["y"] for r in rs), max(r["y"] for r in rs)],
            "font": dict(collections.Counter(
                r["font"] for r in rs).most_common()),
        } for k, rs in by.items()},
        "ambiguous": {t: sorted(k) for t, k in ambiguous.items()},
    }
    return md, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=None, help="旧版 capture 文件")
    ap.add_argument("--regions", default=None,
                    help="新版 wite2_regions.tsv（默认自动找游戏目录）")
    ap.add_argument("--capture", action="store_true",
                    help="强制用旧版 capture 分析")
    ap.add_argument("--dump", default=None,
                    help="wite2_text.bin（可选，用来校验链帧）")
    ap.add_argument("--base", default="0x950000",
                    help="运行时基址，默认 0x950000（由 4450BC 唯一命中反推）")
    ap.add_argument("--md", default=os.path.join(ROOT, "build", "region_report.md"))
    ap.add_argument("--json", default=os.path.join(ROOT, "build", "region_report.json"))
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--all", action="store_true",
                    help="不过滤屏幕坐标（默认只看界面层）")
    args = ap.parse_args()
    args.ui_only = not args.all

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    rev = load_rev_dict()

    # 新版优先
    regions = args.regions
    if regions is None and not args.capture:
        if os.path.exists(DEFAULT_REGIONS):
            regions = DEFAULT_REGIONS
    if regions and os.path.exists(regions):
        md, data = report_regions(regions, args, rev)
        if md is None:
            return 2
        os.makedirs(os.path.dirname(args.md), exist_ok=True)
        with io.open(args.md, "w", encoding="utf-8") as f:
            f.write(md)
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(md)
        print("-> %s" % args.md)
        print("-> %s" % args.json)
        return 0

    if args.regions:
        print("找不到 regions 文件: %s" % args.regions)
        return 2

    # 旧版 capture 分析
    inp = args.inp or (DEFAULT_IN if os.path.exists(DEFAULT_IN) else FALLBACK_IN)
    if not os.path.exists(inp):
        print("找不到 capture: %s" % inp)
        return 2
    rows = load_capture(inp)
    if not rows:
        print("capture 是空的: %s" % inp)
        return 2

    checker = Checker(args.dump, int(args.base, 16))

    def show(s):
        """中文 [英文] 形式，方便对照。"""
        en = rev.get(s)
        return "%s [%s]" % (s, en) if en and en != s else s

    # ---------- 按 this 分组 ----------
    by_this = collections.OrderedDict()
    for r in rows:
        by_this.setdefault(r["this"] or "?", []).append(r)

    # ---------- 按链分组（这里是区域的主要候选） ----------
    by_chain = collections.OrderedDict()
    for r in rows:
        key = ":".join(r["chain"][:3]) if r["chain"] else "?"
        by_chain.setdefault(key, []).append(r)

    # ---------- 跨区域重复文本 ----------
    text_regions = collections.defaultdict(set)
    for r in rows:
        if not looks_like_text(r["src"]):
            continue
        text_regions[r["src"]].add(r["this"] or "?")
    ambiguous = {t: rs for t, rs in text_regions.items() if len(rs) > 1}

    out = []
    w = out.append
    w("# 绘制区域报告\n")
    w("数据源: `%s`  " % inp)
    w("捕获 %d 条，%d 个 this，%d 条链前缀  \n"
      % (len(rows), len(by_this), len(by_chain)))
    w("> 链帧标记: `+` = 前一字节是 E8 call，像返回地址；"
      "`?` = 落在 .text 但前一条不是 E8（可能是间接调用，也可能是假阳性）；"
      "无标记 = 没给 --dump，未校验。\n")

    # ---------- 区域明细 ----------
    w("\n## 一、按 this 分组（一个 this = 一个界面/文档实例）\n")
    for this, rs in sorted(by_this.items(), key=lambda kv: -len(kv[1])):
        texts = [r["src"] for r in rs if looks_like_text(r["src"])]
        uniq = list(dict.fromkeys(texts))
        name, hits = classify(uniq)
        chains = collections.Counter(
            ":".join(r["chain"][:3]) for r in rs if r["chain"])
        w("\n### this=%s   %d 条（有文本 %d 条）" % (this, len(rs), len(uniq)))
        if name:
            w("- 疑似界面: **%s**（命中: %s）"
              % (name, "、".join(hits[:6])))
        w("- 出现链:")
        for ch, n in chains.most_common(4):
            w("  - `%s` × %d" % (ch or "?", n))
        w("- 文本样本:")
        for s in sorted(uniq, key=len, reverse=True)[:args.samples]:
            w("  - %s" % show(s))
        w("")

    # ---------- 链明细 ----------
    w("\n## 二、按链前缀分组（同一套绘制路径 = 同一个位置类别）\n")
    w("链只有 3 帧，粒度和 this 相当；要更细得靠坐标/字体，"
      "现有 capture 里没有。\n")
    for ch, rs in sorted(by_chain.items(), key=lambda kv: -len(kv[1])):
        texts = list(dict.fromkeys(
            r["src"] for r in rs if looks_like_text(r["src"])))
        name, hits = classify(texts)
        thises = collections.Counter(r["this"] or "?" for r in rs)
        w("\n### `%s`   %d 条" % (ch, len(rs)))
        frames = ch.split(":") if ch != "?" else []
        if frames:
            w("- 帧: %s" % "  ".join(
                "%s%s" % (f, checker.verdict(int(f, 16))) for f in frames))
        if name:
            w("- 疑似界面: **%s**（命中: %s）"
              % (name, "、".join(hits[:6])))
        w("- this: %s" % ", ".join("%s×%d" % (t, n)
                                   for t, n in thises.most_common(3)))
        w("- 文本样本:")
        for s in sorted(texts, key=len, reverse=True)[:args.samples]:
            w("  - %s" % show(s))
        w("")

    # ---------- 歧义清单 ----------
    w("\n## 三、跨区域重复文本（需要按界面分译的候选）\n")
    if ambiguous:
        w("| 文本 | 英文 | 出现在几个区域 | 区域 this |")
        w("|---|---|---|---|")
        for t, rs in sorted(ambiguous.items(), key=lambda kv: -len(kv[1]))[:60]:
            w("| %s | %s | %d | %s |"
              % (t, rev.get(t, ""), len(rs),
                 ", ".join(sorted(rs)[:4])))
    else:
        w("没有跨区域重复的文本 —— 这本身也是个信号："
          "当前 capture 只覆盖了少数界面。")

    # ---------- 绘制序列 ----------
    w("\n## 四、绘制顺序（this 切换点 = 换界面/换面板）\n")
    prev = None
    seg = []
    segs = []
    for r in rows:
        if r["this"] != prev:
            if seg:
                segs.append((prev, seg))
            prev = r["this"]
            seg = []
        seg.append(r)
    if seg:
        segs.append((prev, seg))
    for this, rs in segs[:30]:
        texts = [r["src"] for r in rs if looks_like_text(r["src"])]
        head = " / ".join(texts[:4]) or "(无数值以外的文本)"
        w("- `%s` × %d: %s" % (this, len(rs), head[:120]))

    # ---------- 落盘 ----------
    md = "\n".join(out) + "\n"
    os.makedirs(os.path.dirname(args.md), exist_ok=True)
    with io.open(args.md, "w", encoding="utf-8") as f:
        f.write(md)

    data = {
        "source": inp,
        "total": len(rows),
        "base": args.base,
        "by_this": {t: {
            "count": len(rs),
            "texts": list(dict.fromkeys(
                r["src"] for r in rs if looks_like_text(r["src"]))),
            "chains": [{"chain": ch, "count": n} for ch, n in
                       collections.Counter(
                           ":".join(r["chain"][:3]) for r in rs
                           if r["chain"]).most_common()],
        } for t, rs in by_this.items()},
        "by_chain": {ch: {
            "count": len(rs),
            "texts": list(dict.fromkeys(
                r["src"] for r in rs if looks_like_text(r["src"]))),
            "this": [{"this": t, "count": n} for t, n in
                     collections.Counter(
                         r["this"] or "?" for r in rs).most_common()],
        } for ch, rs in by_chain.items()},
        "ambiguous": {t: sorted(rs) for t, rs in ambiguous.items()},
    }
    with io.open(args.json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(md)
    print("-> %s" % args.md)
    print("-> %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
