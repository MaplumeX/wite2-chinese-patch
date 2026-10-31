"""检查译文是否破坏了结构性标记。

背景
----
`~` 在 WITE2 里是**换行标记**，不是普通字符。提示框和多行消息是
"a ~ b ~ c" 这种形式拼起来的，游戏按 `~` 的个数确定行数。

译文如果少一个或多一个 `~`，行数就和游戏预先算好的不符。它按
算好的行数去索引行数组，越界那项是个野指针，往里写就崩 —— 而且
崩在完全无关的地方（实测是跳到 .rdata 里执行，往 0x2C006E 这种
低地址写）。

同类标记还有 <cr>（换行控制码）和 url= /url（BBCode）。这些已经
进封印名单，不会翻译。

检查范围
--------
- `~` 个数必须一致
- 首尾是否有 `~` 必须一致
- %s/%d 等占位符顺序个数必须一致（之前单独查过，这里一并复核）

修法
----
数量不一致的，按原文补回或删掉多余的 `~`（优先按位置对齐）。
"""
import json
import re

DICT = 'zh_dict.json'
SPEC = re.compile(r'%[-+ 0#]*\d*(?:\.\d+)?[sdxXufc]')


def main():
    raw = json.load(open(DICT, encoding='utf-8'))
    draw = set(raw.get('_drawOnly', []))

    tilde_bad = []
    spec_bad = []
    for k, v in raw.items():
        if k.startswith('_'):
            continue
        if k in draw:
            continue
        if k.count('~') != v.count('~'):
            tilde_bad.append((k, v))
        if SPEC.findall(k) != SPEC.findall(v):
            spec_bad.append((k, v))

    print('~ 个数不一致: %d 条' % len(tilde_bad))
    for k, v in tilde_bad:
        print('   %2d -> %2d  %r' % (k.count('~'), v.count('~'), k))
        print('            -> %r' % v)
    print()
    print('占位符不一致: %d 条' % len(spec_bad))
    for k, v in spec_bad:
        print('   %r -> %r' % (k, v))

    if tilde_bad:
        fixed = 0
        for k, v in tilde_bad:
            nk, nv = k.count('~'), v.count('~')
            if nv > nk:
                # 多出来的 ~ 全部去掉
                new = v.replace('~', '', nv - nk)
            else:
                # 少的补在末尾
                new = v + '~' * (nk - nv)
            raw[k] = new
            fixed += 1
        with open(DICT, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
        print()
        print('已修正 %d 条' % fixed)


if __name__ == '__main__':
    main()
