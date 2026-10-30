#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧本描述汉化

Dat/Scen/*.txt 是明文的历史描述文件, 游戏直接读取。
翻译后按原行结构逐行替换, 保持段落和换行不变。

用法:
    python tools/scen_localize.py apply     翻译并写入游戏目录
    python tools/scen_localize.py check     只检查覆盖率和格式
    python tools/scen_localize.py restore   还原原始文件
"""

import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
DEST = os.path.join(GAME, "Dat", "Scen")

sys.path.insert(0, ROOT)
from scen_trans import TR  # noqa: E402

STRUCT = {
    "Historical Overview": "历史背景",
    "Scenario Overview": "剧本概览",
    "Axis Player:": "轴心玩家：",
    "Soviet Player:": "苏联玩家：",
    "Axis player:": "轴心玩家：",
    "Soviet player:": "苏联玩家：",
    "---------------------------------": "---------------------------------",
    "----------------------------------": "----------------------------------",
}


def translate_line(line):
    """返回翻译后的行, 未命中返回 None"""
    body = line.rstrip("\r\n")
    ending = line[len(body):]
    stripped = body.strip()

    if not stripped:
        return line                      # 空行原样

    if stripped in STRUCT:
        indent = body[:len(body) - len(body.lstrip())]
        return indent + STRUCT[stripped] + ending

    if stripped.startswith("- APs/Turn"):
        return body.replace("- APs/Turn", "- 行政点/回合") + ending

    if stripped.startswith("Author:") or stripped.startswith("Authors:"):
        # 作者名保留原样, 只把前缀汉化
        if stripped.startswith("Authors:"):
            return body.replace("Authors:", "作者：", 1) + ending
        return body.replace("Author:", "作者：", 1) + ending

    if stripped in TR:
        indent = body[:len(body) - len(body.lstrip())]
        return indent + TR[stripped] + ending

    return None


def iter_files():
    """直接读游戏目录, 仓库里不放游戏原文"""
    for f in sorted(glob.glob(os.path.join(DEST, "*.txt"))):
        yield os.path.basename(f), f


def cmd_check():
    total = miss = 0
    problems = []
    for name, path in iter_files():
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                total += 1
                if translate_line(line) is None:
                    miss += 1
                    problems.append((name, i, line.rstrip()))

    print("原始文件: %d 个, 非空行 %d" % (len(list(iter_files())), total))
    print("未翻译: %d" % miss)
    for n, i, l in problems[:20]:
        print("   %s:%d %r" % (n, i, l))
    return 0 if miss == 0 else 1


def cmd_apply():
    if not os.path.isdir(DEST):
        print("找不到 %s" % DEST)
        return 1

    # 首次运行先备份原始文件
    bak = os.path.join(ROOT, "_scen_backup")
    if not os.path.isdir(bak):
        os.makedirs(bak)
        for name, _ in iter_files():
            src = os.path.join(DEST, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(bak, name))
        print("已备份原始文件 -> _scen_backup/")

    done = miss = 0
    for name, _ in iter_files():
        path = os.path.join(bak, name) if os.path.exists(
            os.path.join(bak, name)) else os.path.join(DEST, name)
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()

        out = []
        for line in lines:
            t = translate_line(line)
            if t is None:
                out.append(line)
                miss += 1
            else:
                out.append(t)
                if t != line:
                    done += 1

        target = os.path.join(DEST, name)
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(out)

    print("已翻译 %d 行, 未翻译 %d 行" % (done, miss))
    print("写入 %s" % DEST)
    return 0


def cmd_restore():
    bak = os.path.join(ROOT, "_scen_backup")
    if not os.path.isdir(bak):
        print("没有备份, 无法还原")
        return 1
    n = 0
    for f in glob.glob(os.path.join(bak, "*.txt")):
        shutil.copy2(f, os.path.join(DEST, os.path.basename(f)))
        n += 1
    print("已还原 %d 个文件" % n)
    return 0


def main():
    cmds = {"apply": cmd_apply, "check": cmd_check, "restore": cmd_restore}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        return 1
    return cmds[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
