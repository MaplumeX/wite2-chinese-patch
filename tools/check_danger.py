#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测危险词条

.rdata 里有些字符串不是给玩家看的，而是：
  1. 配置键名（游戏用它读 settings 文件）—— 改了会丢设置，比如声音
  2. 代码里硬编码引用的默认值（mov edx, 0x1109870）—— 改了长度变化会出错
  3. 内部标识符 / 枚举名

这些改了会让游戏行为异常，但界面上看不出来。这个工具用三条规则识别它们：

规则 A（已废弃）: 出现在 .text 段的立即数里
    实测发现几乎所有 .rdata 字符串地址都会被菜单/绘制代码引用，这是正常的
    显示路径，不能作为危险判据。

规则 B: 像标识符（驼峰/全小写无空格 + 长度较短）
    SoundFx / SfxGroupVolume / buildLimit / evActive 这类。
    玩家看到的文本几乎总是有空格或首字母大写。

规则 C: 在配置文件里作为键名出现
    wfgamedata.txt / SoundFXConfig.txt / GuiFonts.json 等。

白名单: SAFE_LABELS
    形式像标识符但确认是显示文本的条目（FrRec / NONE / AirGroups）。
    判断依据是 .rdata 里的上下文——邻居如果是 Depots/Range/MP
    这类 UI 标签，说明是表格列标题；如果邻居是 IMPASSABLE/SAND
    或 //Tiles// 路径，说明是枚举或资源名。

规则 D: 全大写地形/资源枚举名
    WATER / MOUNTAIN / RIVER 这类，游戏用它们查找地块图形资源。
    改了会让地图渲染异常——实测 WATER -> 水域 导致水体六角格变黑。

用法:
    python tools/check_danger.py              列出危险词条
    python tools/check_danger.py --fix        从 zh_dict.json 删除它们
    python tools/check_danger.py --fix --dry  只看会删哪些
"""

import glob
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DICT = os.path.join(ROOT, "zh_dict.json")
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"

IMAGE_BASE = 0xBE0000


def load_sections():
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
    return d, secs


def load_code():
    """运行时 dump 的解密代码段；没有就返回 None"""
    p = os.path.join(GAME, "wite2_text.bin")
    if not os.path.exists(p):
        return None
    return open(p, "rb").read()


def find_string_rvas(body, key):
    """返回 key 作为完整 NUL 串出现时的所有 RVA（相对 .rdata 起点）"""
    pat = key.encode("utf-16-le")
    out = []
    st = 0
    while True:
        i = body.find(pat, st)
        if i < 0:
            break
        st = i + 1
        if i >= 2 and body[i - 2:i] != b"\x00\x00":
            continue
        j = i + len(pat)
        if j + 2 <= len(body) and body[j:j + 2] != b"\x00\x00":
            continue
        out.append(i)
    return out


def referenced_by_code(code, rva_in_rdata, rd_va):
    """该字符串的绝对地址是否出现在 .text 的立即数里"""
    if code is None:
        return False
    va = IMAGE_BASE + rd_va + rva_in_rdata
    return struct.pack("<I", va) in code


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def looks_like_identifier(k):
    """真正的程序标识符: 驼峰、含下划线、或全小写长词。

    排除首字母大写的普通单词（Supply / Moscow / February），
    那些是显示文本，不是标识符。
    """
    if k in SAFE_LABELS:
        return False
    if not IDENT.match(k):
        return False
    # 纯大写短词（OKH/TOE/FAT）是界面标签
    if k.isupper() and len(k) <= 6:
        return False
    # 含下划线 -> 程序标识符
    if "_" in k:
        return True
    # 驼峰（小写后接大写，如 SoundFx / buildLimit）
    if re.search(r"[a-z][A-Z]", k):
        return True
    # 全小写长词：只有配置文件里确实作为键名出现才算（由规则 C 判定）。
    # 这里不单独判 True —— "partisan"/"infantry" 这种普通英文单词
    # 首字母小写但也是显示文本。
    return False


# 地形/图形/资源枚举名。这些全大写短词被游戏用来查找地块图形资源
# 和地形类型，改了会导致地图渲染异常（例如水体变黑）。
# 纯地形枚举。改了会让地图渲染拿不到 //Tiles//<地形>// 下的图形，
# 水体变黑就是这么来的（WATER / RIVER）。
#
# 注意 PORT 和 NONE 不在这里。它们是资源/工厂类型名，虽然也在 .rdata
# 出现，但 patch_section 只替换整串（前后必须是 NUL），路径里的
# //Tiles//PORT// 前面是 '/'，永远不会被命中，所以安全。fb79222 版
# 实测无副作用，不要因为名字全大写就误判。
ENUM_TERRAIN = {
    "WATER", "CLEAR", "ROUGH", "MOUNTAIN", "SWAMP", "WOODS", "FOREST",
    "CITY", "URBAN", "BOCAGE", "TUNDRA", "DESERT", "SAND", "IMPASSABLE",
    "POLDER", "HVY_URBAN", "HEAVY_URBAN", "RIVER", "RAIL", "ROAD",
    "BORDER", "COAST", "TOWN", "AIRFIELD", "SITE", "DEPOT",
    "MAJOR", "MINOR", "NON_MOT", "MOT",
}


# 白名单：形式像标识符/枚举，但确认是玩家可见的显示文本。
# 每一条都经过上下文核实（例如 FrRec 的邻居是 Depots/Range/MP，
# 是补给表格的列标题，不是配置键）。
SAFE_LABELS = {
    "FrRec", "FrLost", "VehRec",          # 补给表格列标题
    "NONE",                                # 下拉框选项
    "AirGroups",                           # 界面标签（无空格版本）
    "UNITS", "CONDITION",
    # Köppen 气候分类代号（.data 里和 Polar Maritime 等长名配对显示）
    "mP", "mT", "mA", "cP", "cT",
    # 部队编制缩写，只出现在部队名后缀里（"leFH Howitzer Battalion"）。
    # 已核实不在 .rdata 作为独立枚举出现；zbV 例外，它是空军部队
    # 类型枚举（邻居 FAGr/Transport/TG/NJG），所以没进词典。
    "PzG", "leFH", "sFH",
}

def data_only_keys():
    """zh_dict.json 里 _dataOnly 列出的键：只改写 .data 段。

    这些键在 .data 是定长显示数组，在 .rdata 另有同名枚举副本
    （例如 PORT 的邻居是 AIRFIELD/UNIT/RAILWAY/FERRY/INTERDICT，
    是任务类型枚举）。已用作用域隔离，不算危险词条。
    """
    try:
        with open(os.path.join(ROOT, "zh_dict.json"), encoding="utf-8") as f:
            return set(json.load(f).get("_dataOnly", []))
    except Exception:
        return set()


def is_enum_name(k):
    """全大写枚举名（WATER / MOUNTAIN / RIVER ...）"""
    if k in SAFE_LABELS:
        return False
    return k in ENUM_TERRAIN


def config_keys():
    """从游戏配置文件收集键名"""
    keys = set()
    for pat in ("*.txt", "*.json", "*.ini"):
        for p in glob.glob(os.path.join(GAME, pat)) + \
                 glob.glob(os.path.join(GAME, "Dat", pat)):
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            keys.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", txt))
    return keys


def analyze():
    d, secs = load_sections()
    rd_va, rd_vs, rd_raw, rd_rs = secs[".rdata"]
    body = d[rd_raw:rd_raw + rd_rs]
    code = load_code()
    cfg = config_keys()

    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)

    scoped = data_only_keys()
    findings = []
    for k in dic:
        if k.startswith("_"):
            continue
        # 已用 _dataOnly 做作用域隔离的键不参与判定：
        # 它们只改写 .data 的显示数组，碰不到 .rdata 的枚举副本。
        if k in scoped:
            continue
        reasons = []

        if is_enum_name(k):
            reasons.append("地形/资源枚举")

        if looks_like_identifier(k):
            reasons.append("标识符")

        # 规则 C: 只认"看起来像程序标识符"的配置键。
        # 普通英文单词（Supply/Moscow）也会出现在配置文件的注释里，
        # 不能一律算键名。
        if k in cfg and looks_like_identifier(k):
            reasons.append("配置键名")

        # 规则 A 已废弃：几乎所有 .rdata 字符串地址都会被菜单/绘制代码引用，
        # 这是正常的显示路径，不代表危险。真正危险的是"被当作标识符使用"，
        # 由规则 B 和 C 覆盖。

        if reasons:
            findings.append((k, dic[k], reasons))

    return findings, code is None


def cmd_fix(dry):
    findings, no_code = analyze()
    if not findings:
        print("没有发现危险词条")
        return 0

    with open(DICT, encoding="utf-8") as f:
        dic = json.load(f)

    by_reason = {}
    for k, v, rs in findings:
        for r in rs:
            by_reason.setdefault(r, []).append(k)

    print("发现 %d 条危险词条：" % len(findings))
    for r, ks in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        print("  %-16s %d 条" % (r, len(ks)))
    print()

    for k, v, rs in findings:
        print("  %-30r -> %-16s [%s]" % (k, v, ",".join(rs)))

    if dry:
        print("\n(dry run，未修改)")
        return 0

    for k, _, _ in findings:
        del dic[k]
    with open(DICT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dic, f, ensure_ascii=False, indent=2)
    print("\n已从词典删除 %d 条，剩余 %d 条"
          % (len(findings), len([x for x in dic if not x.startswith("_")])))
    if no_code:
        print("注意：没有 wite2_text.bin，规则 A 未生效")
    return 0


def main():
    if "--fix" in sys.argv:
        return cmd_fix("--dry" in sys.argv)
    findings, no_code = analyze()
    print("危险词条 %d 条：" % len(findings))
    for k, v, rs in findings:
        print("  %-30r -> %-16s [%s]" % (k, v, ",".join(rs)))
    if no_code:
        print("\n注意：没有 wite2_text.bin，规则 A（代码硬编码引用）未生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
