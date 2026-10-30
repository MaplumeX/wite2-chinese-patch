"""回归用例跑的是 **真实 C 匹配器**，不是 Python 复刻。

早期这里是一份 hook_text.c 的 Python 复刻。它和 C 代码早就漂移了：
复刻全绿，实际 DLL 是错的，XXXXVI Mot. Cr 修了三轮才对上。
现在改成把用例喂给 build/selftest.exe（同一份 hook_text.c 编译出来的
命令行程序），拿它的真实输出和期望值比。没有第二份实现可以漂移。
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
    # (输入, 期望输出, 说明)
    ("Starting player", "起始玩家", "必须整词匹配, 不能变成 开始ing player"),
    ("Start", "开始", "独立单词应正常翻译"),
    ("National", "National", "不能变成 国家al"),
    ("Nation", "国家", "独立单词应正常翻译"),
    ("Army Group Center", "中央集团军群", "完整名"),
    ("Army Grp. Sout", "南方集团军群", "缩写+截断"),
    ("1st Panzer Grou", "第1装甲集群", "截断"),
    ("113th ID", "第113步兵师", "数字+兵种缩写"),
    ("16th MD", "第16摩托化师", "数字+兵种缩写"),
    ("LI Corps (1/5)", "第51军 (1/5)", "罗马番号 + 括号后缀"),
    ("Army", "集团军", "独立单词, 精确匹配"),
    ("End", "结束", "独立单词"),
    ("Ending", "Ending", "不能变成 结束ing"),
    ("Supporting", "Supporting", "不能变成 支援ing"),
    ("Support", "支援", "独立单词"),
    ("Mapping", "Mapping", "不能变成 地图ping"),
    ("Map", "地图", "独立单词"),
    # 一词多义: 最长匹配优先
    ("Front Armor", "前装甲", "Front 在此处不是方面军"),
    ("Front", "方面军", "独立出现时是苏军编制"),
    # 单复数一致
    ("Air Group", "飞行队", "单数"),
    ("Air Groups", "飞行队", "复数必须和单数一致"),
    ("Air Base", "航空基地", "单数"),
    ("Air Bases", "航空基地", "复数必须和单数一致"),
    # 尾部空格 / 扩展名: 剧本名从 .scn 文件名派生，
    # 界面用空格填充对齐，绘制出的串常带尾部空格。
    ("1941 Campaign", "1941年战役", "整串精确"),
    ("1941 Campaign ", "1941年战役 ", "尾部空格要保留对齐"),
    ("1941 Campaign   ", "1941年战役   ", "多个尾部空格也要保留"),
    ("1941 Campaign.scn", "1941年战役", "剥离扩展名"),
    ("1941 Campaign .scn", "1941年战役", "扩展名前有空格"),
    ("foo.scn", "foo.scn", "词典里没有的不能乱改"),
    # 部队名: 番号 + 兵种短语拆开处理
    ("3rd Panzer Division", "第3装甲师", "阿拉伯数字番号"),
    ("XXIV Panzer Corps", "第24装甲军", "罗马数字转阿拉伯"),
    ("HQ Panzer Corps", "司令部装甲军", "两字符前缀走小表"),
    ("41 Tier 1a Panzer Division", "41型1a装甲师", "Tier 层级标记"),
    ("German City Fortress", "德国城市要塞", "非数字前缀"),
    ("5th Guards Airborne Corps", "第5近卫空降军", "近卫部队"),
    ("Rum. Mountain Division", "罗马尼亚山地师", "国籍 + 兵种"),
    ("Foo Bar Baz", "Foo Bar Baz", "无法识别的不动"),
    # OOB 树形列表: 前导缩进 / 尾随空格 / 层级标记都必须原样保留
    (" XXIV Mot. Corps ", " 第24摩托化军 ", "前导缩进 + 尾随空格"),
    (" 255th Infantry Division ", " 第255步兵师 ", "缩进 + 番号"),
    ("  XII Corps ", "  第12军 ", "多空格缩进"),
    ("- 602nd Army SP Light Flak Battalion ",
     "- 第602集团军属自行轻型高炮营 ", "层级标记 -"),
    # 罗马数字是部队正式番号，原样保留，不能换算成阿拉伯数字
    ("XXIV Mot. Corps", "第24摩托化军", "罗马数字转阿拉伯"),
    ("XII Corps", "第12军", "短罗马数字"),
    ("LI Corps", "第51军", "LI = 51"),
    ("XXXXVIII Panzer Corps", "第48装甲军", "非标准罗马写法"),
    # 逐词分支也要转数字，否则 "- 654th ..." 里的 654th 会原样保留
    ("- 654th Mot. Panzerjaeger Battalion",
     "- 第654摩托化装甲猎兵营", "层级标记后的数字番号"),
    # 拆分编号：游戏把部队拆成 1/2/3 三个分遣队，斜杠左边是序号不是番号。
    # 判据是斜杠右边必须是"数字+序数后缀"，否则左边就是番号。
    ("3/213th Security Division", "3/第213警备师", "拆分编号保留"),
    ("2/6th Guards", "2/第6近卫", "拆分编号 + 词典命中的尾串"),
    ("1/998th Ost", "1/第998东方", "拆分编号 + 词典命中的尾串"),
    ("1/West Wall", "第1西墙", "斜杠右边非番号时左边是番号"),
    # 数字 + 兵种缩写要支持拆分编号前缀和尾随空格。之前 try_numbered
    # 要求整串严格是"数字+序数+空格+缩写"，"3/97th ID" 前缀对不上、
    # "97th ID " 尾随空格对不上，两条都退回英文。
    ("3/97th ID", "3/第97步兵师", "拆分编号 + 兵种缩写"),
    ("97th ID", "第97步兵师", "兵种缩写基本形式"),
    ("3/97th PD", "3/第97装甲师", "拆分编号 + 装甲师缩写"),
    ("97th ID ", "第97步兵师 ", "尾随空格保留"),
    # NKVD 是通行缩写，部队名里保留不译（用户指定）
    ("22nd NKVD", "第22 NKVD", "NKVD 保留缩写"),
    # 尾部截断：游戏固定宽度控件会砍掉长名字的末尾，砍完查不到词典
    # 就退回英文，英文更长又被继续砍。加前缀回退打破这个循环。
    ("18th Panzer Divisio", "第18装甲师", "尾部截断（缺 n）"),
    ("1/213th Sec Div", "1/第213警备师", "尾部截断 + 拆分编号"),
    ("1st Panzer Grou", "第1装甲集群", "尾部截断（缺 p）"),
    ("Panzer Divisio", "装甲师", "整串截断，走策略 4"),
    # 截断点必须落在词中间。"Armored" 是完整单词，配到 "Armored Cars"
    # 会得出 "第17装甲车"，那是误配，宁可保留英文。
    ("XVII Armored", "XVII Armored", "截断点不在词中间，不配"),
    # 后缀精确命中时，番号部分查不到是正常的（地名不翻），不能拦。
    # 这条曾经被"番号全未译就放弃切分"的判据误伤。
    ("Andrews Field", "安德鲁斯机场", "地名 + 精确命中后缀"),
    # 番号部分改贪心最长匹配后，多词术语不再被拆散
    ("West Wall Mot. Brandenburg Regiment", "西墙摩托化勃兰登堡团", "多词术语整段命中"),
    ("Vallo Alpino Mot. Brandenburg Regiment", "阿尔卑斯防线摩托化勃兰登堡团", "同上"),
    ("II 'Folgore' Mountain Battalion", "第二「闪电」山地营", "引号师名整串命中"),
    ("MG-Ski zbV Mot. Regiment (-)", "机枪滑雪营特设摩托化团（减）", "缩写 + 缩减标记"),
    # 斜杠分隔的双番号
    ("CCXVIII/43rd", "第218/第43", "罗马/阿拉伯双番号"),
    ("LXXXVIII/43rd", "第88/第43", "同上"),
    # 裸番号（OBs.csv 没 Suffix 时显示名就是番号）
    ("17th", "第17", "裸番号"),
    # ===== 数字必须原样 =====
    # 策略 3.2 曾经直接用 conv_num_prefix，它接受纯数字，结果界面上
    # 所有数字都变成番号：飞机数量 218 显示成 "第218"。
    # 策略 3.1 同样问题："218/350"（飞机可用/总数）→ "第218/第350"。
    # 策略 1.7 同样问题："12 turns" → "第12回合"。
    ("218", "218", "飞机数量"),
    ("0", "0", "零"),
    ("1939", "1939", "年份"),
    ("218/350", "218/350", "可用/总数比值"),
    ("12/45", "12/45", "纯数字比值"),
    ("12 turns", "12 回合", "格式串模板"),
    ("45%", "45%", "百分比"),
    # 兵种缩写（窄控件里的缩写形式，数据里没有 Mnt 全拼）
    ("4th Mnt Div", "第4山地师", "山地师缩写"),
    ("4th Mtn Div", "第4山地师", "山地师缩写变体"),
    ("4th Inf Div", "第4步兵师", "步兵师缩写"),
    # 游戏有 118 对硬编码缩写（exe 0x5CEC14），同一部队有两种形态。
    # 之前只翻全称，缩写形态整片漏掉 —— 这是"手写了译文却还看到英文"
    # 的真正原因。缩写译文由 tools/abbr_variants.py 从全称复用生成。
    ("9th Pz Div", "第9装甲师", "缩写 Pz Div"),
    ("113th Inf Div", "第113步兵师", "缩写 Inf Div"),
    ("Mech Div", "机械化师", "缩写 Mnt/Mech"),
    ("IT Mnt Div", "意大利山地师", "国籍缩写 + 兵种缩写"),
    ("4th Mnt Di", "第4山地师", "缩写 + 截断"),
    # 句点粘连：游戏吃掉缩写点后的空格（"Mot. Corps" -> "Mot.Corps"），
    # 多句点的还连成一片（"Mot.Nbl.W.Bn"）。切分点和分词器都要认句点，
    # 句点留给前一个词，这样贪心匹配取原文切片能拼回完整条目。
    ("XXXXVI Mot.Cr", "第46摩托化军", "句点粘连 + 截断"),
    ("XXXXVI Mot.Corps", "第46摩托化军", "句点粘连"),
    ("XXXXVI Pz.Cr", "第46装甲军", "句点粘连 + 缩写"),
    ("1 Mot.Nbl.W.Bn", "第1摩托化火箭炮营", "多句点缩写"),
    ("II 'Folgore' Mountain Battalion", "第二「闪电」山地营", "贪心短语优先于单 token 数字转换"),
    # 以下来自 2024-10 的游戏内全量捕获（tests/capture_2024-10.txt）
    ("XXXXVI Mot. Cr", "第46摩托化军", "游戏丢弃缩写点后的空格"),
    ("XXXXVI Mot.Cr", "第46摩托化军", "缩写点后无空格"),
    ("1 Mot.Nbl.W.Bn", "第1摩托化火箭炮营", "多句点缩写 + 2 字符词条"),
    ("  1st Mountain Division   ( 27.6 / 13.8  100% )   MP 16 ",
     "  第1山地师   ( 27.6 / 13.8  100% )   移动力16", "统计行分段翻译"),
    ("  Germany(Lublin) - Poor 道路 ", "  德国(卢布林) - 劣质 道路", "国家(城市) - 质量"),
    ("Franz Halder  (Generaloberst)", "Franz Halder  (大将)", "姓名不译，军衔要译"),
    ("Fedor von Bock", "Fedor von Bock", "将领姓名保留原文"),
    ("1-17th Rum. Mot. Anti-tank Company", "1-第17罗马尼亚摩托化反坦克连", "连字符分遣序号"),
    ("16th(a) Infantry Division", "第16(a)步兵师", "括号内单字母"),
    ("Wins : 0  Losses: 0", "胜 : 0  损失: 0", "战果行"),
    # 数据文件里的显示文本（Nations/GroundTypes/AirProfiles）
    ("Soviet Union", "苏联", "国家名"),
    ("Rifle Squad", "步兵班", "地面单位类型（Rifle 统一译步兵）"),
    ("CW Heavy Bomber", "英联邦重型轰炸机", "机型分类"),
    ("255th Infantry Division", "第255步兵师", "阿拉伯数字照常转"),
    # 序数后缀 st/nd/rd/th 全部要能识别。C 里边界写成 i+2 < len 时
    # 只有 1st 侥幸通过，79th 会退回逐词翻译变成 "79th步兵师"。
    ("1st Panzer Division", "第1装甲师", "序数 st"),
    ("2nd Panzer Division", "第2装甲师", "序数 nd"),
    ("3rd Panzer Division", "第3装甲师", "序数 rd"),
    ("79th Infantry Division", "第79步兵师", "序数 th"),
    ("113th Infantry Division", "第113步兵师", "三位数 th"),
]



def main():
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
