"""回归用例跑的是 **真实 C 匹配器**，不是 Python 复刻。

早期这里是一份 hook_text.c 的 Python 复刻。它和 C 代码早就漂移了：
复刻全绿，实际 DLL 是错的。现在改成把用例喂给 build/selftest.exe
（同一份 hook_text.c 编译出来的命令行程序），拿它的真实输出和期望值比。
没有第二份实现可以漂移。

用例范围 = 现在 C 侧还保留的五条策略：
  1   整串精确匹配
  1.5 剥尾部空格后精确匹配
  1.6 剥文件扩展名后精确匹配
  1.4 格式串模板匹配
  2   剥尾部后缀后精确匹配（尾巴原样保留）

部队番号（"113th ID"、"Army Group Center"、缩略/截断形式）不再翻译，
用例只用来确认它们**原样返回**，不会被误翻或拼装。
"""

import os
import subprocess
import sys

NL = chr(10)
TAB = chr(9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "build", "selftest.exe")

CASES = [
    # ---- 词边界：绝不能做子串替换 ----
    ("Starting player", "起始玩家", "必须整词匹配, 不能变成 开始ing player"),
    ("Start", "开始", "独立单词应正常翻译"),
    ("National", "National", "不能变成 国家al"),
    ("Nation", "国家", "独立单词应正常翻译"),
    ("End", "结束", "独立单词"),
    ("Ending", "Ending", "不能变成 结束ing"),
    ("Supporting", "Supporting", "不能变成 支援ing"),
    ("Support", "支援", "独立单词"),
    ("Mapping", "Mapping", "不能变成 地图ping"),
    ("Map", "地图", "独立单词"),
    ("Show Production screen", "显示生产界面", "整句可译时才译, 不做分段拼接"),
    # ---- 最长匹配优先 ----
    ("Front Armor", "前装甲", "Front 在此处不是方面军"),
    # ---- 单复数一致（编制类词条已移除，改用界面词） ----
    ("Supply", "补给", "单数"),
    ("Depots", "补给站", "复数必须和单数一致"),
    # ---- 尾部空格 / 扩展名 ----
    ("1941 Campaign", "1941年战役", "整串精确"),
    ("1941 Campaign ", "1941年战役 ", "尾部空格要保留对齐"),
    ("1941 Campaign   ", "1941年战役   ", "多个尾部空格也要保留"),
    ("1941 Campaign.scn", "1941年战役", "剥离扩展名"),
    ("1941 Campaign .scn", "1941年战役", "扩展名前有空格"),
    ("1941 Campaign - No Early End", "1941年战役 - 无提前结束", "整串精确"),
    ("foo.scn", "foo.scn", "词典里没有的不能乱改"),
    # ---- 格式串模板 ----
    ("12 turns", "12 回合", "占位符当通配符"),
    ("Max TOE %", "最大编制 %", "百分号占位"),
    ("Command Points Limit:", "指挥点上限：", "标签带冒号"),
    ("Ground Defense Value %", "地面防御值 %", "百分号占位"),
    # ---- 数字必须原样（不做番号换算） ----
    ("218", "218", "飞机数量"),
    ("0", "0", "零"),
    ("1939", "1939", "年份"),
    ("218/350", "218/350", "可用/总数比值"),
    ("12/45", "12/45", "纯数字比值"),
    ("45%", "45%", "百分比"),
    ("0-50", "0-50", "数值范围不是番号"),
    ("21-16", "21-16", "数值范围不是番号"),
    ("17th", "17th", "裸番号不再换算"),
    # ---- 部队番号：一律原样返回 ----
    ("Army Group Center", "Army Group Center", "具体编制名"),
    ("Army Grp. Sout", "Army Grp. Sout", "缩写+截断形式"),
    ("Army Grp. Cent", "Army Grp. Cent", "缩写+截断形式"),
    ("113th ID", "113th ID", "数字+兵种缩写"),
    ("16th MD", "16th MD", "数字+兵种缩写"),
    ("LI Corps (1/5)", "LI Corps (1/5)", "罗马番号 + 括号后缀"),
    ("XXIV Panzer Corps", "XXIV Panzer Corps", "罗马数字番号"),
    ("3rd Panzer Division", "3rd Panzer Division", "阿拉伯数字番号"),
    ("255th Infantry Division", "255th Infantry Division", "阿拉伯数字照常不译"),
    (" XXIV Mot. Corps ", " XXIV Mot. Corps ", "前导缩进 + 尾随空格"),
    ("3/97th ID", "3/97th ID", "拆分编号 + 兵种缩写"),
    ("1/1st Infantry Division", "1/1st Infantry Division", "拆分编号原样"),
    ("1/West Wall", "1/West Wall", "斜杠右边非番号"),
    ("18th Panzer Divisio", "18th Panzer Divisio", "尾部截断不补全"),
    ("XXXXVI Mot.Cr", "XXXXVI Mot.Cr", "句点粘连"),
    ("1st Sep. Division", "1st Sep. Division", "缩写点"),
    ("II 'Folgore' Mountain Battalion", "II 'Folgore' Mountain Battalion",
     "引号师名"),
    ("16th(a) Infantry Division", "16th(a) Infantry Division", "括号内单字母"),
    ("102nd RHG Command", "102nd RHG Command", "具体番号"),
    # ---- 姓名 / 统计行 ----
    ("Fedor von Bock", "Fedor von Bock", "将领姓名保留原文"),
    ("  1st Mountain Division   ( 27.6 / 13.8  100% )   MP 16 ",
     "  1st Mountain Division   ( 27.6 / 13.8  100% )   MP 16 ",
     "统计行整串原样"),
    # ---- 数据文件里的界面文本 ----
    ("Soviet Union", "苏联", "国家名"),
    ("CW Heavy Bomber", "英联邦重型轰炸机", "机型分类"),
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if not os.path.exists(EXE):
        print("缺少 %s" % EXE)
        print("先跑: build.bat selftest")
        return 2
    inp = NL.join(c[0] for c in CASES) + NL
    r = subprocess.run([EXE], input=inp.encode("utf-8"),
                       stdout=subprocess.PIPE, cwd=os.path.join(ROOT, "build"))
    out = r.stdout.decode("utf-8", "replace").splitlines()
    if len(out) != len(CASES):
        print("输出行数不对: %d vs %d" % (len(out), len(CASES)))
        return 2
    ok = bad = 0
    for (src, want, note), line in zip(CASES, out):
        parts = line.split(TAB)
        got = parts[1] if len(parts) > 1 else ""
        if got == want:
            ok += 1
            print("  OK   %-52s -> %s" % (src, got))
        else:
            bad += 1
            print("  FAIL %-52s -> %s   期望 %s   (%s)" % (src, got, want, note))
    print()
    print("%d 通过, %d 失败" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
