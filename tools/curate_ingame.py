#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人工策展：从 155 条里挑出玩家真能看到的

自动分类器在 618 条 `ui_msg` 里已经分不下去了 —— 里面混着：

  * 编辑器输入提示（`Enter Map Top X`）—— 只在编辑器出现
  * 场景校验日志（`Unit ID %d: %s has no valid leader assigned`）—— 只在
    「Scenario Check」报告里，玩家正常游戏看不到
  * CSV 列名（`renameUnit,`、`id,newName,origName,...`）
  * 服务端/加密内部错误（`Can't acquire cryptographic provider`）
  * 报表日志（`Renamed on Turn %d`）

这份清单是**人工过完**的结果，只保留正常游戏流程里会弹出来/画出来的。

分类：
    server    服务器/对战相关弹窗
    dialog    确认框
    admin     行政点不足/命令被拒
    limit     建造数量上限
    tooltip   鼠标悬停提示（含 ~ 换行）
    notify    游戏内通知消息（回合结算、部队事件）
    ui        控件标签/提示

用法:
    python tools/curate_ingame.py            # 列清单
    python tools/curate_ingame.py --csv x.csv
"""

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (地址, 类别, 说明) —— 地址来自 build/rea/strings_authoritative.json
CURATED = [
    # ---------- server：PBEM / 服务器对战 ----------
    ("0x9d2f7c", "server", "无法连接服务器"),
    ("0x9d337c", "server", "找不到游戏（对战列表）"),
    ("0x9d3568", "server", "找不到挑战"),
    ("0x9dbb50", "server", "无法进行此游戏"),
    ("0x9dbb38", "server", "载入游戏失败"),
    ("0x9db958", "server", "无法获取 Steam 用户"),
    ("0x9d37c8", "server", "上传失败 E3/E1/E2/E5/E6/E4"),
    ("0x9d3960", "server", "同上"),
    ("0x9d3a1c", "server", "同上"),
    ("0x9d3b14", "server", "同上"),
    ("0x9d3b84", "server", "同上"),
    ("0x9d3aa0", "server", "下载失败 E4"),
    ("0x94b304", "server", "服务器操作发生意外错误"),
    ("0x94b2e4", "server", "不是你的回合"),

    # ---------- admin：行政点 ----------
    ("0x9b42b0", "admin", "此命令需要 %d 行政点！"),
    ("0x9cc448", "admin", "动员此部队需要 %d 行政点！！"),
    ("0x9c99e8", "admin", "不允许 - 需要 %d 行政点"),
    ("0x9cb4d0", "admin", "行政点不足"),
    ("0x9b3d08", "admin", "行政点不足"),

    # ---------- limit：建造上限 ----------
    ("0x98cc80", "limit", "要塞部队建造上限"),
    ("0x9c1da0", "limit", "同名部队数量上限"),
    ("0x9533a0", "limit", "飞机数量不足"),
    ("0x953410", "limit", "护航飞机数量不足"),

    # ---------- dialog：确认框 ----------
    ("0x932ed0", "dialog", "补给优先级确认"),
    ("0x9c0500", "dialog", "删除胜利城市确认"),
    ("0x9d19fc", "dialog", "删除标签确认"),
    ("0x9c31f0", "dialog", "输入六角格坐标（多行）"),

    # ---------- tooltip：悬停提示（~ = 换行）----------
    ("0x994460", "tooltip", "设置延迟 - 部队到达"),
    ("0x99a000", "tooltip", "设置延迟 - 飞行队到达"),
    ("0x99b628", "tooltip", "设置撤离 - 飞行队撤离"),
    ("0x994390", "tooltip", "设置延迟 - 部队（先手/后手）"),
    ("0x999f28", "tooltip", "设置延迟 - 飞行队（先手/后手）"),
    ("0x99b6d8", "tooltip", "设置撤离 - 飞行队（先手/后手）"),
    ("0x94d528", "tooltip", "在地图上点击设置"),
    ("0x94d458", "tooltip", "在地图上点击选择坐标"),
    ("0x985e38", "tooltip", "点击设置筛选条件"),
    ("0x9d61f0", "tooltip", "点击清除筛选条件"),
    ("0x94aa20", "tooltip", "点击编辑"),
    ("0x9c3350", "ui", "点击输入搜索字符串"),
    ("0x982ef8", "tooltip", "设置两栖准备度"),
    ("0x982f8c", "tooltip", "设置战斗准备度"),
    ("0x93c334", "notify", "国家设定为投降"),

    # ---------- notify：游戏内通知 ----------
    ("0x97bb98", "notify", "部队被歼灭/无法撤退"),
    ("0x950cd0", "notify", "飞行队转移失败"),
    ("0x9c1d60", "notify", "部队设置为转移"),
    ("0x9459e8", "notify", "将由计划改名创建"),
    ("0x97ecf0", "notify", "不再冻结"),
    ("0x95d9b8", "notify", "回合事件未通过概率判定"),
    ("0x998a40", "notify", "飞机设为档案"),
    ("0x9b31c4", "notify", "找不到作战群（AOG）"),

    # ---------- ui：控件标签 ----------
    ("0x92c1e0", "ui", "取消选择全部飞行队"),
    ("0x95ed20", "ui", "按 ENTER 退出"),
    ("0x99f0d8", "ui", "延迟部队被忽略"),
    ("0x944e08", "ui", "设置当前/最大移动力"),
    ("0x9361e8", "ui", "设置回合"),
    ("0x9369d8", "ui", "设置延迟"),
    ("0x9362c0", "ui", "设置回合失效"),
    ("0x9c49c8", "ui", "设置货船数"),
    ("0x9c49fc", "ui", "设置运兵船数"),

    # ---------- 存档 / 载入失败 ----------
    ("0x94a988", "error", "载入剧本失败"),
    ("0x94a8f8", "error", "保存临时文件失败"),
    ("0x9b36c8", "error", "载入游戏失败"),
    ("0x9b2d04", "error", "无法创建文件夹"),
    ("0x96??", "error", "（占位，占下条）"),
    ("0x9602e8", "error", "存档失败"),
    ("0x96032c", "error", "载入失败"),
    ("0x933bd8", "error", "无法保存"),
    ("0x985fd4", "error", "无法打开文件"),
    ("0x9b38a8", "error", "地面数据完整性校验失败"),
    ("0x9b38d0", "error", "完整性校验失败"),
    ("0x985c58", "error", "导出失败"),
    ("0x9b204c", "error", "找不到城市"),
    ("0x942470", "error", "OB 条目重复"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    args = ap.parse_args()

    auth = json.load(open(os.path.join(ROOT, "build", "rea",
                                       "strings_authoritative.json"),
                          encoding="utf-8"))
    by_addr = {s["addr"].lower(): s["text"] for s in auth}

    rows = []
    for addr, kind, note in CURATED:
        if addr == "0x96??":
            continue
        t = by_addr.get(addr.lower())
        if t is None:
            print("!! 地址对不上: %s" % addr, file=sys.stderr)
            continue
        rows.append({"kind": kind, "addr": addr, "text": t, "note": note})

    from collections import Counter
    c = Counter(r["kind"] for r in rows)
    print("人工策展：%d 条玩家可见、未收录的串\n" % len(rows))
    for k in ("server", "admin", "limit", "dialog", "tooltip",
              "notify", "ui", "error"):
        if c.get(k):
            print("  %-8s %3d" % (k, c[k]))

    print()
    for r in rows:
        print("  [%-7s] %s  %r" % (r["kind"], r["addr"], r["text"]))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["kind", "addr", "text", "note"])
            w.writeheader()
            w.writerows(rows)
        print("\n-> %s" % args.csv)


if __name__ == "__main__":
    main()
