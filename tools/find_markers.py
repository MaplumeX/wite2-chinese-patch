"""列出所有「会写内存」的条目里，形如控制标记 / 格式标记的那些。

判据
----
显示文本是句子或短语。而下面这些形状的串不是：

  含 < >      控制标记，如 <cr> 换行、<b> 加粗
  含 [ ]      BBCode 标记，如 [url]
  含 =        key=value 片段
  只含小写+标点、长度很短    内部 token

这类串游戏是拿去**解析**的，不是拿去画的。改成中文后解析失败，
调用方拿到 NULL 或走到错误分支，通常在后续绘制文本时崩。
"""
import json
import re

RAW = json.load(open('zh_dict.json', encoding='utf-8'))
DRAW = set(RAW.get('_drawOnly', []))

rows = []
for line in open('wite2_zh.tsv', encoding='utf-8'):
    if line.startswith('#'):
        continue
    p = line.rstrip('\n').split('\t')
    if len(p) < 2:
        continue
    k, v = p[0], p[1]
    sc = p[2] if len(p) > 2 else ''
    if sc in ('t', 'x'):
        continue
    rows.append((k, v))

def classify(k):
    if any(c in k for c in '<>'):
        return '控制标记 <>'
    if any(c in k for c in '[]'):
        return 'BBCode []'
    if '=' in k:
        return 'key=value'
    if re.fullmatch(r"[a-z0-9_.\-:*#/ ]+", k) and len(k) <= 8:
        return '内部 token'
    return None

hits = [(classify(k), k, v) for k, v in rows if classify(k)]
print('会写内存 %d 条，其中形似标记 %d 条' % (len(rows), len(hits)))
print()
for c, k, v in sorted(hits, key=lambda t: t[0]):
    print('  [%-12s] %-24r -> %r' % (c, k, v))
