#!/usr/bin/env python3
"""
从备份还原游戏数据文件

用法:
  python tools/restore_game.py            还原 GenData/*.dat 和 CSV
  python tools/restore_game.py --list     只列出备份内容
"""
import os, shutil, sys, glob

GAME = r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BAK = os.path.join(ROOT, "_game_backup")


def main():
    gendata_bak = os.path.join(BAK, "GenData")
    csv_bak = os.path.join(BAK, "CSV_Generic")

    if not os.path.isdir(gendata_bak):
        print("找不到备份目录:", gendata_bak)
        return 1

    files = sorted(glob.glob(os.path.join(gendata_bak, "*.dat")))
    if "--list" in sys.argv:
        print("备份的 .dat 文件 (%d 个):" % len(files))
        for f in files:
            print("  %-24s %8d 字节" % (os.path.basename(f), os.path.getsize(f)))
        return 0

    # 1) 还原 GenData/*.dat
    dst_dir = os.path.join(GAME, "Dat", "GenData")
    n = 0
    for src in files:
        dst = os.path.join(dst_dir, os.path.basename(src))
        shutil.copy2(src, dst)
        n += 1
    print("还原 %d 个 .dat 文件 -> %s" % (n, dst_dir))

    # 2) 还原导出的 CSV（清空后复制）
    if os.path.isdir(csv_bak):
        dst_csv = os.path.join(GAME, "Dat", "CSV", "Generic")
        os.makedirs(dst_csv, exist_ok=True)
        for f in os.listdir(dst_csv):
            if f.lower().endswith(".csv"):
                os.remove(os.path.join(dst_csv, f))
        m = 0
        for src in glob.glob(os.path.join(csv_bak, "*.csv")):
            shutil.copy2(src, os.path.join(dst_csv, os.path.basename(src)))
            m += 1
        print("还原 %d 个 CSV 文件 -> %s" % (m, dst_csv))

    print("\n完成。建议重启游戏验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
