"""把 .rdata 里的「字符表/日期表」项改为只绘制层。

依据
----
tools/find_stride_real.py 扫出的定步长表里，落在 .rdata 最前面
（0x38EA / 0x6A38 / 0xB698 / 0x120D0 / 0x1A5F8 / 0x125DC / 0x126A4）
的是 C 运行库的字符表和 locale 表：

  .rdata+0x120D0 步长8   '"', '#', '$', '%', '&'        标点表
  .rdata+0x125DC 步长56  'Thursday', 'Jan', 'Aug', 'February'
  .rdata+0x126A4 步长36  'April', 'August', 'October',
                         'December', 'MM/dd/yy'          ← 日期表

而且真实作用域检查显示，这些表里**只有两样**还在被改写：

  '&'        -> '与'
  'December' / 'February' / 'January' / 'November' /
  'October'  / 'September'  -> 中文

标点表被拿去做字符分类/折行判断，改成汉字会让分类函数走到别的分支。
日期表紧挨着 'MM/dd/yy'，是**解析**用的模板 —— 指挥官报告要显示日期、
要按月份排序，解析失败就拿不到值。

改法：这两类降到绘制层。内存里保持英文，界面照常显示中文。
"""
import json
import os

DICT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "zh_dict.json")
DICT = os.path.normpath(DICT)

TARGETS = [
    "&",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday",
]


def main():
    raw = json.load(open(DICT, encoding="utf-8"))
    draw = raw.setdefault("_drawOnly", [])
    have = set(draw)

    n = 0
    for k in TARGETS:
        if k in raw and not k.startswith("_") and k not in have:
            draw.append(k)
            have.add(k)
            n += 1
            print("  %-12r -> %-10r 降级为绘制层" % (k, raw[k]))
        elif k in have:
            print("  %-12r 已经是绘制层" % k)

    with open(DICT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    print()
    print("降级 %d 条，_drawOnly 现有 %d 条" % (n, len(draw)))


if __name__ == "__main__":
    main()
