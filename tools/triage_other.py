#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 `other` 桶拆开 —— 它是个垃圾桶，混了四类完全不同的东西

之前的 coverage_report 把 3644 条都归到 `other`，于是：

  * 部队番号（`Panzer Division`、`Army Group North`）—— 按项目约定不翻
  * 将领姓名（`Fedor von Bock`）—— 不翻
  * 地名（`North Paris`、`W Berlin`）—— 不翻
  * 第三方库错误（zlib `invalid distance code`、WinHTTP、CRT errno）—— 玩家看不到
  * **游戏自己的报错/提示（`Can't play this game!`、`Unable to contact server!`）**

最后那一类是**真的该翻而没翻**的，但被前三类淹没了 3600 多条，
每次看报告都滑过去。

用法:
    python tools/triage_other.py
    python tools/triage_other.py --csv build/rea/other.csv
    python tools/triage_other.py --show ui
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AUTH = os.path.join(ROOT, "build", "rea", "strings_authoritative.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

# ---- 第三方库的痕迹：出现这些就一定是库内部消息，玩家永远看不到 ----
LIB = re.compile(
    r"zlib|inflate|deflate|WinHTTP|WINHTTP_|Transfer-Encoding|base64|B64|"
    r"UTF-8|UTF-16|TiXml|CDATA|JSON|json|DirectX|D3DFMT|IGraph|IID_|"
    r"strerror|locale|URI |hexadecimal|streambuf|stream buffer|"
    r"constructor|destructor|vftable|vbase|RTTI|dynamic_cast|"
    r"\.log|\.txt|\.dll|engine\.log|sndDebug|readme|"
    r"IDispatch|ShellExecute|Microsoft |SOFTWARE\\\\|Samples Path|"
    r"chore|task|buffer needs|already allocated|"
    r"Operation not permitted|No such file|Interrupted function|"
    r"Input/output error|Bad file descriptor|Permission denied|"
    r"No space left|Read-only file|Broken pipe|Directory not empty|"
    r"Illegal byte sequence|Resource temporarily|Filename too long",
    re.I,
)

# ---- 第三方 CRON/errno 表（一整段连续的经典 errno 文案）----
ERRNO = re.compile(
    r"^(Unknown error|Result too large|Domain error|File exists|Improper link|"
    r"No such device|Not a directory|Is a directory|Invalid argument|"
    r"Too many open files.*|Inappropriate I/O.*|File too large|Invalid seek|"
    r"Too many links|No locks available|Function not implemented|"
    r"Resource device|No child processes|Not enough space|Bad address|"
    r"No such process|Arg list too long|Exec format error)$"
)

# ---- 部队番号 / 编制类型：项目约定不翻（README「为什么部队番号不翻」）----
UNIT = re.compile(
    r"^(I{1,3}|IV|V|VI{1,3}|IX|X{1,4}|L?X{0,3}[IVX]{0,3})?\s*"
    r"(Guards |Gds |Afr\. |Africa March |Tunis T\d+ |LW |SS |NKVD |PVO |"
    r"Rum\. |Hun\. |Ital\. |Fin\. |Slov\. |Croat\. |BR |US |CA |FF |PO |"
    r"Fallschirm)?\s*"
    r"(\d+\w*\s+)?"
    r"(Panzer|PzG|Panzerj|Panzergrenadier|Stug|Sturm|Rifle|Cavalry|Tank|"
    r"Mechanized|Motorized|Infantry|Grenadier|Volksgrenadier|Security|"
    r"Mountain|Jaeger|Sk|Airborne|Artillery|Howitzer|Gun|Mortar|Rocket|"
    r"Flak|AA|Anti|AT|Sapper|Engineer|Pioneer|Signal|Recon|Armoured|Armored|"
    r"Tank Destroyer|Combat Engineer|Railway|Construction|Partisan|Border|"
    r"Fortified|Shock|Air|Aviation|Fighter|Bomber|Support|Transport|"
    r"Corps|Division|Brigade|Regiment|Battalion|Army|Group|Front|Command|"
    r"District|Zone|Region|Reserve|Regia|Luftflotte|Flieger|Fliegerkorps|"
    r"Jagd|Kommando|Feldherrnhalle|Grossdeutschland|Omar|Courtney|Leonard|"
    r"Joseph|Charles|Walton|George|Dwight|Maitland|Gerard|Henry|Guy|"
    r"Mieczyslaw|Oliver|Harold|Troy|Gilbert|Wade|Bernard|Miles|John|"
    r"Richard|Mark|Lucian|Willis|Walter|Gotthard|Rudolf|Maximilian|"
    r"Hermann|Fedor|Georg|Ernst|Guenther|Hans|Ewald|Erich|Werner|Italo|"
    r"Anton|Friedrich|Wilhelm|Bernard|Mikolaj)\b"
)

# ---- 编辑器控件占位标签 ----
EDITOR_TAG = re.compile(r"^<[^>]+>$")

# ---- 玩家可见的报错 / 提示（这些该翻）----
USER_MSG = re.compile(
    r"(Unable to|Can't |Cannot |Failed to|Failed |Wrong |Invalid |Unexpected |"
    r"Not enough|Please |Click to|Press |Select |Enter |Choose |No |"
    r"Are you|Do you|Confirm|Warning|Error|requires |is ignored|"
    r"Delayed|delete|rename|Set )",
    re.I,
)

# ---- 将领姓名（两个以上的词，且每个词首字母大写、无编制关键词）----
PERSON = re.compile(r"^([A-Z][a-z]+\.?\s+){1,3}[A-Z][a-z]+(-[A-Z][a-z]+)?$")


def load_tsv(path):
    out = set()
    UN = {"t": "\t", "n": "\n", "\\": "\\"}

    def un(s):
        r = []
        i = 0
        while i < len(s):
            c = s[i]
            if c == "\\" and i + 1 < len(s):
                r.append(UN.get(s[i + 1], "\\" + s[i + 1]))
                i += 2
            else:
                r.append(c)
                i += 1
        return "".join(r)

    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line or line[0] == "#":
            continue
        esc = line[0] == "@"
        body = line[1:] if esc else line
        if "\t" not in body:
            continue
        k = body.split("\t", 1)[0]
        out.add((un(k) if esc else k).lower())
    return out


def classify(t):
    if LIB.search(t):
        return "lib"
    if ERRNO.match(t):
        return "lib"
    if EDITOR_TAG.match(t):
        return "editor_tag"
    if USER_MSG.search(t) and not UNIT.match(t):
        return "ui_msg"
    if UNIT.match(t):
        return "unit"
    if PERSON.match(t):
        return "person"
    # 纯技术常量
    if re.match(r"^[A-Za-z0-9_\.\\%\{\}\[\]\-\+/: ]+$", t) and not re.search(r"\s[a-z]{4,}\s", t):
        return "token"
    return "misc"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--show", help="打印某一类")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    auth = json.load(open(AUTH, encoding="utf-8"))
    have = load_tsv(TSV)

    buckets = defaultdict(list)
    for s in auth:
        t = s["text"]
        if t.lower() in have:
            continue
        # 只看 UTF-16 之外的都算 —— 这里全部重分类，不再用旧的 other
        b = classify(t)
        buckets[b].append(s)

    print("按新分类统计未收录（全清单，不限 other）：")
    for b, n in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print("  %-12s %5d" % (b, len(buckets[b])))

    ui = buckets.get("ui_msg", [])
    print("\n★ ui_msg（玩家可见的报错/提示，%d 条）：" % len(ui))
    for s in sorted(ui, key=lambda x: x["text"]):
        print("   %s  %r" % (s["addr"], s["text"][:88]))

    if args.show:
        sel = buckets.get(args.show, [])
        print("\n=== %s（%d）===" % (args.show, len(sel)))
        for s in sel[:args.limit]:
            print("   %s  %r" % (s["addr"], s["text"][:88]))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["bucket", "text", "addr"])
            for b, items in sorted(buckets.items()):
                for s in items:
                    w.writerow([b, s["text"], s["addr"]])
        print("\n-> %s" % args.csv)


if __name__ == "__main__":
    main()
