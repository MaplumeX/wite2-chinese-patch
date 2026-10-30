#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
找出 .rdata 里玩家可见但词典没有的界面文本

比 find_gaps.py 更严格：直接扫 exe 的 .rdata 界面区段，按"像不像显示文本"
过滤，输出未翻译的完整串。

用法:
    python tools/scan_ui.py              列出未翻译的界面文本
    python tools/scan_ui.py --csv        导出 scan_ui.csv 供批量翻译
    python tools/scan_ui.py --stats      只统计
"""

import csv
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"

# 扫描整个 .rdata。之前只扫 0x548000-0x5C6000，
# 漏掉了区段外的界面文本（Check Date / Initiative Player 等）。
UI_LO, UI_HI = 0x50C000, 0x625000

# 明显不是显示文本的模式
SKIP_PAT = [
    r"^//",                      # 资源路径
    r"^gui/", r"^Gui/",
    r"^dat\\", r"^Dat\\",
    r"^\.",                      # 扩展名
    r"^_",                       # 内部前缀
    r"^[a-z]+[A-Z]",             # 驼峰标识符
    r"^[a-z]{1,4}_",             # 小写前缀 + 下划线
    r"\.(bmp|jpg|tga|sav|psv|dat|csv|scn|txt|ini|json)$",
    r"^FAC_",                    # 工厂枚举
    r"^IABR", r"^FIABR",         # 航空单位代号
    r"^(ShAD|ShAK|GShAD|GShAK|PShAD|BAD|BAK|DBAD|DBAK|GBAD|GBAK|GIAD|GIAK|IAD|IAK|SAD|SAK|TBAD|TBAK)( |$)",
    r"^(StG|JG|KG|SG|NJG|ZG|TG|SKG|NSGr|NAGr|FAGr|Koluft|ChF|KBF)( |$|\(|/)",
    r"^(Enter|Input|Eneter) ",   # 编辑器输入提示
    r"^\*\*\*\*\*",              # 编辑器校验
    r"^\d{4} [A-Z]",             # 剧本代号 1941 GC
    r"^(shift|ctrl|alt)-",
    r"^<",                       # 占位符
    r"^%",                       # 格式串开头
    r"^(ERROR|Error) ",
    r"^#",
    r"^~",                       # 片段
]

SKIP_RE = [re.compile(p) for p in SKIP_PAT]
FMT_RE = re.compile(r"%[-0-9.* ]*[sdifxc]")


def load_ui_strings():
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
    sub = body[UI_LO - rd_va:UI_HI - rd_va]

    pat = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")
    out, seen = [], set()
    for m in pat.finditer(sub):
        s = m.group().decode("utf-16-le")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def looks_like_ui(s):
    """判断是否是玩家可见的界面文本"""
    if len(s) < 3 or len(s) > 90:
        return False
    if not re.search(r"[A-Za-z]{2,}", s):
        return False
    if FMT_RE.search(s):
        return False
    for r in SKIP_RE:
        if r.search(s):
            return False
    # 纯大写短代号（OKH/TOE/AFV）是界面标签，保留
    if re.fullmatch(r"[A-Z0-9 /&().\-+*#:%,]+", s):
        return len(s) >= 4
    # 含空格 -> 像短语
    if " " in s:
        return True
    # 无空格：首字母大写且够长，或者带冒号
    if s.endswith(":"):
        return True
    if s[0].isupper() and len(s) >= 5:
        return True
    return False


def load_dict():
    import json
    with open(DICT, encoding="utf-8") as f:
        return json.load(f)


def scan():
    strings = load_ui_strings()
    dic = load_dict()
    keys = set(k for k in dic if not k.startswith("_"))

    # 用大小写不敏感的集合判断，否则 "Fuel production" 会被误报为未翻译，
    # 因为词典里存的是 "Fuel Production"。
    keys_lower = set(k.lower() for k in keys)

    ui = [s for s in strings if looks_like_ui(s)]
    miss = [s for s in ui if s.lower() not in keys_lower]
    miss.sort(key=lambda x: (len(x), x))
    return ui, miss


def main():
    ui, miss = scan()

    if "--stats" in sys.argv:
        print("界面区段字符串 %d 条" % len(ui))
        print("已翻译 %d 条 (%.0f%%)"
              % (len(ui) - len(miss), 100.0 * (len(ui) - len(miss)) / max(1, len(ui))))
        print("未翻译 %d 条" % len(miss))
        return 0

    if "--csv" in sys.argv:
        out = os.path.join(ROOT, "scan_ui.csv")
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["原文", "译文", "长度"])
            for s in miss:
                w.writerow([s, "", len(s)])
        print("导出 %d 条 -> %s" % (len(miss), out))
        return 0

    print("界面文本 %d 条，已翻译 %d，未翻译 %d\n"
          % (len(ui), len(ui) - len(miss), len(miss)))
    for s in miss:
        print(repr(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
