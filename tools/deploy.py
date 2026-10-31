#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署到游戏目录

复制: version.dll, wite2_zh.tsv, Dat/Fonts/wite2_zh.ttf
改写: Dat/GuiFonts.json 里所有字体指向 WITE2ZH

用法:
    python tools/deploy.py            部署
    python tools/deploy.py --check    只检查部署状态
    python tools/deploy.py --restore  还原 GuiFonts.json（保留补丁文件）
"""

import glob
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
FAMILY = "WITE2ZH"
FONT_FILE = "wite2_zh.ttf"

# 需要复制的文件: (源, 目标相对路径)
COPIES = [
    (os.path.join(ROOT, "build", "version.dll"), "version.dll"),
    (os.path.join(ROOT, "wite2_zh.tsv"), "wite2_zh.tsv"),
    # 数据层映射表（.dat 整句翻译）。不存在也能跑，只是数据层不生效。
    (os.path.join(ROOT, "wite2_dat.tsv"), "wite2_dat.tsv"),
    (os.path.join(ROOT, "Dat", "Fonts", FONT_FILE),
     os.path.join("Dat", "Fonts", FONT_FILE)),
]

GUI_FONTS = os.path.join(GAME, "Dat", "GuiFonts.json")


def game_exists():
    if not os.path.isdir(GAME):
        print("找不到游戏目录: %s" % GAME)
        print("请修改本脚本顶部的 GAME 变量")
        return False
    return True


def patch_gui_fonts(dry=False):
    """把 GuiFonts.json 里所有字体的 name/file 指向我们的子集字体"""
    if not os.path.exists(GUI_FONTS):
        print("找不到 GuiFonts.json")
        return 0

    with open(GUI_FONTS, encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != FAMILY:
            entry["name"] = FAMILY
            changed += 1
        if entry.get("file") != FONT_FILE:
            entry["file"] = FONT_FILE
            changed += 1

    if changed and not dry:
        bak = GUI_FONTS + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(GUI_FONTS, bak)
        with open(GUI_FONTS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print("GuiFonts.json: %d 个字体 -> %s%s"
          % (len(data), FAMILY, "（已是最新）" if not changed else ""))
    return changed


def cmd_deploy():
    if not game_exists():
        return 1

    ok = True
    for src, rel in COPIES:
        if not os.path.exists(src):
            print("[缺失] %s" % os.path.relpath(src, ROOT))
            ok = False
            continue
        dst = os.path.join(GAME, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except PermissionError:
            print("[占用] %s（游戏正在运行？）" % rel)
            ok = False
            continue
        print("部署 -> %s (%d 字节)" % (rel, os.path.getsize(dst)))

    patch_gui_fonts()

    # 清理可能残留的实验性开关文件
    for name in ("wite2_scan.ini", "wite2_collect.ini", "wite2_dryrun.ini",
                 "wite2_prose.ini", "wite2_regions.ini"):
        p = os.path.join(GAME, name)
        if os.path.exists(p):
            os.remove(p)
            print("移除实验开关 -> %s" % name)

    return 0 if ok else 1


def cmd_check():
    if not game_exists():
        return 1

    print("游戏目录: %s\n" % GAME)
    for _, rel in COPIES:
        p = os.path.join(GAME, rel)
        src = next(s for s, r in COPIES if r == rel)
        if not os.path.exists(p):
            print("  [未部署] %s" % rel)
            continue
        same = (os.path.getsize(p) == os.path.getsize(src))
        print("  [%s] %-28s %d 字节"
              % ("一致" if same else "不同", rel, os.path.getsize(p)))

    if os.path.exists(GUI_FONTS):
        with open(GUI_FONTS, encoding="utf-8") as f:
            data = json.load(f)
        n = sum(1 for e in data.values()
                if isinstance(e, dict) and e.get("name") == FAMILY)
        print("\n  GuiFonts.json: %d/%d 个字体指向 %s"
              % (n, len(data), FAMILY))

    # 剧本描述
    scen = glob.glob(os.path.join(GAME, "Dat", "Scen", "*.txt"))
    zh = 0
    for p in scen:
        try:
            if any("\u4e00" <= c <= "\u9fff"
                   for c in open(p, encoding="utf-8").read()):
                zh += 1
        except OSError:
            pass
    print("  剧本描述: %d/%d 个文件已汉化" % (zh, len(scen)))
    return 0


def cmd_restore():
    bak = GUI_FONTS + ".bak"
    if os.path.exists(bak):
        shutil.copy2(bak, GUI_FONTS)
        print("已还原 GuiFonts.json")
    else:
        print("没有备份可还原")
    print("补丁文件（version.dll / wite2_zh.tsv / 字体）需手动删除")
    return 0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--check":
        return cmd_check()
    if arg == "--restore":
        return cmd_restore()
    return cmd_deploy()


if __name__ == "__main__":
    sys.exit(main())
