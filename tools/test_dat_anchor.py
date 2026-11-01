#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据层匹配的回归测试：前缀误伤

背景
----
明文里的字符串是 `[4字节字符数][UTF-16 正文]`。早期的匹配器不认长度，
从某处开始"一直扫到遇见非法码点"，于是：

  * 短键会命中长记录的前缀。'Italian Campaign' 把
    'Italian Campaign Advances' 的前 16 字符改写成中文 + 空格，
    留下 '意大利战役        dvances'。这条名字随后要按名字查 map，
    查不到就抛 std::out_of_range，玩家看到 runtime error。
  * 扫描越过记录末尾，键的尾巴多带一个字符，永远匹配不上。

现在改成以长度前缀为锚。这个测试用**合成的容器**把这个场景固化下来，
不需要游戏文件：造一个 .scn，用真实的 dattest.exe（真实 dat_layer.c）
跑一遍，检查逐字节等于预期。

用例
----
1. 完整记录 == 键                -> 必须替换，补空格
2. 长记录以短键为前缀            -> 必须原样不动
3. 正文内部出现短键文本          -> 必须原样不动
4. 奇数偏移上的完整记录          -> 必须替换（.scn 里就是奇数偏移）
5. 键是另一条记录的中间片段      -> 必须原样不动

用法:
    python tools/test_dat_anchor.py
"""

import io
import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from dat_crypto import KEY, rc4  # noqa: E402

# GBK 控制台直接 print \u2717 会 UnicodeEncodeError，和 check_hook_addrs.py 同样处理
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                              # noqa: BLE001
    pass

DATTEST = os.path.join(ROOT, "build", "dattest.exe")


def rec(text, parity=0):
    """把一段文字编成一条记录：[len][utf-16]，可指定奇偶对齐"""
    pad = b"" if parity == 0 else b"\x00"
    body = text.encode("utf-16-le")
    return pad + struct.pack("<I", len(text)) + body


def main():
    if not os.path.isfile(DATTEST):
        print("缺少 %s，先跑 build\\t2.bat" % DATTEST)
        return 2

    # dattest.exe 是用真实的 dat_layer.c / inflate.c 编出来的。源码改过却忘了
    # 重编，这个测试就会拿旧二进制"通过"，比不测更糟。
    newest = max(os.path.getmtime(os.path.join(ROOT, "src", f))
                 for f in ("dat_layer.c", "inflate.c"))
    if os.path.getmtime(DATTEST) < newest:
        print("✗ dattest.exe 比 src/*.c 旧，先重编：build\\t2.bat")
        return 2

    KEYTXT = "Italian Campaign"
    LONG = "Italian Campaign Advances"
    BODY = ("The campaign continues. " + KEYTXT + " is now active. " * 2).strip()
    MID = "Prefix " + KEYTXT + " Suffix"

    # 明文：头 + 五种用例。奇数偏移的用例前面多补 1 字节。
    plain = bytearray(b"_2by3 WITE2_SF\0" + b"\x00" * (0x100 - 15))
    plan = []

    def add(text, parity, expect):
        blob = rec(text, parity)
        plain.extend(blob)
        # 记下正文起始的绝对偏移。不能用 find() 反查 —— 用例 1 和 用例 4
        # 的文本相同，find 会两次都命中第一处，预期就漏掉了奇数偏移那条。
        plan.append((text, expect, len(plain) - len(text) * 2))

    add(KEYTXT, 0, True)          # 1 完整记录
    add(LONG, 0, False)           # 2 长记录，键是它的前缀
    add(BODY, 0, False)           # 3 正文里含键文本
    add(KEYTXT, 1, True)          # 4 奇数偏移的完整记录
    add(MID, 0, False)            # 5 键在中间

    m = {KEYTXT: "意大利战役"}
    exp = bytearray(plain)
    # 预期输出：把「完整记录」那两处换成译文并补空格
    for text, expect, off in plan:
        if expect:
            val = (m[text] + " " * (len(text) - len(m[text]))).encode("utf-16-le")
            exp[off:off + len(text) * 2] = val

    tmp = tempfile.mkdtemp(prefix="w2anchor_")
    scen_dir = os.path.join(tmp, "scen")
    os.makedirs(scen_dir, exist_ok=True)
    scen = os.path.join(scen_dir, "synthetic.scn")
    with open(scen, "wb") as f:
        # 必须是 gzip 容器（wbits=31），不是裸 zlib（wbits=15）
        co = zlib.compressobj(6, zlib.DEFLATED, 31)
        gz = co.compress(bytes(plain)) + co.flush()
        f.write(rc4(KEY, gz))

    mapfile = os.path.join(tmp, "map.tsv")
    with io.open(mapfile, "w", encoding="utf-8", newline="\n") as f:
        for k, v in m.items():
            f.write("%s\t%s\n" % (k, v))

    out = os.path.join(tmp, "out.bin")
    r = subprocess.run([DATTEST, scen, mapfile, out],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if not os.path.isfile(out):
        print("dattest 失败:\n" + (r.stdout or "") + (r.stderr or ""))
        return 1
    with open(out, "rb") as f:
        got = f.read()

    ok = True
    if got != bytes(exp):
        ok = False
        d = [i for i in range(min(len(exp), len(got))) if exp[i] != got[i]]
        print("✗ 输出与预期不符，首个差异 0x%X（共 %d 字节）"
              % (d[0] if d else 0, len(d)))
        i = max(0, (d[0] if d else 0) - 40)
        print("  预期:", bytes(exp[i:i + 120]).decode("utf-16-le", "replace"))
        print("  实际:", got[i:i + 120].decode("utf-16-le", "replace"))

    # 分用例报告
    for idx, (text, expect, _off) in enumerate(plan, 1):
        if expect:
            want = (m[KEYTXT] + " " * (len(text) - len(m[KEYTXT])))
            hit = want.encode("utf-16-le") in got
        else:
            hit = text.encode("utf-16-le") in got
        flag = "OK  " if hit else "FAIL"
        if not hit:
            ok = False
        print("  %s 用例%d  %-30r %s"
              % (flag, idx, text[:28], "已替换" if expect else "保持原样"))

    print()
    print("命中数: %s" % [l for l in (r.stdout or "").splitlines() if "命中" in l])
    print("=== 通过 ===" if ok else "=== 失败 ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
