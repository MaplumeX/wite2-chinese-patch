#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 src/hook_text.c 里所有硬编码地址都是**合法的 RVA**

为什么需要
----------
这个项目里存在两套地址体系：

    Ghidra / REA 看到的      VA = 镜像基址 + RVA = 0x400000 + RVA
    DLL 里必须用的是          RVA（因为 HMODULE 已经是运行时基址）

两者差 0x400000。写错一次的表现是：

    code=C0000005 addr=010DF280 access=EXEC

即运行时跳到了 exe 镜像之外，游戏直接崩。这个错误真发生过一次
（`exe + 0x85F280` 应该是 `exe + 0x45F280`），而且靠肉眼 review
没看出来 —— 因为两个数字看着都"像"。

为什么不能用"落在某个段内"当判据
--------------------------------
`.data` 的 VirtualSize 是 **0x5AF1FAC（约 95 MB）** —— 那是游戏的大型
静态内存池。0x85F280 数值上确实落在 `.data` 的范围里，所以"段内检查"
会放行它，但它在运行时是**零填充的 BSS，不可执行**，跳过去必然
`EXEC` 访问违规。实测这个判据抓不到那个 bug。

所以改用**白名单 + 段类型**双重约束：

  * 每个硬编码地址必须显式登记在 KNOWN_CODE 或 KNOWN_DATA 里
  * KNOWN_CODE 的地址必须落在 `.text`，且字节签名必须匹配
  * KNOWN_DATA 的地址必须落在可写段（.data/.rdata）

新增钩子时必须手动登记 —— 这正是我们想要的摩擦。

用法:
    python tools/check_hook_addrs.py
"""

import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "hook_text.c")
DEFAULT_EXE = os.path.join(ROOT, "build", "WarInTheEast2.decrypted.exe")

# ---- 代码地址白名单：RVA -> (期望字节, 说明, 调用约定) ----
KNOWN_CODE = {
    0x45CE60: (bytes.fromhex("83EC38568BF1"), "draw 绘制入口", "thiscall"),
    0x45F5F0: (bytes.fromhex("558BECFF750C"), "fopen 转发", "__stdcall (ret 8)"),
    0x45F280: (bytes.fromhex("558BEC"), "fopen 实现", "__cdecl"),
    0x45EB80: (bytes.fromhex("558BEC5356"), "fread", "?"),
}

# ---- 数据地址白名单：RVA -> 说明 ----
KNOWN_DATA = {
    0x0E72800: "UI manager 根对象",
}

WRITABLE = {".data", ".rdata"}


def load_pe(path):
    blob = open(path, "rb").read()
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    optsz = struct.unpack_from("<H", blob, pe + 20)[0]
    tb = pe + 24 + optsz
    segs = []
    for i in range(nsec):
        o = tb + i * 40
        name = blob[o:o + 8].rstrip(b"\0").decode("latin1")
        vs, rva, rawsz, rawptr = struct.unpack_from("<IIII", blob, o + 8)
        segs.append({"name": name, "rva": rva, "vs": vs,
                     "raw": rawptr, "rawsz": rawsz})
    return blob, segs


def find_section(segs, rva):
    """按 VirtualSize 找归属段（用于报告，不做通过/不通过的判据）"""
    for s in segs:
        if s["rva"] <= rva < s["rva"] + s["vs"]:
            return s
    return None


def bytes_at(blob, segs, rva, n):
    """只有落在文件实际映射范围内才能读到字节"""
    s = find_section(segs, rva)
    if not s:
        return None
    delta = rva - s["rva"]
    if delta >= s["rawsz"]:
        return None                     # 属于 BSS，文件里没有对应字节
    off = s["raw"] + delta
    if off < 0 or off + n > len(blob):
        return None
    return blob[off:off + n]


def main():
    # GBK 控制台下 ✓/✗ 会 UnicodeEncodeError，强制 UTF-8 输出
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--src", default=SRC)
    args = ap.parse_args()

    for p in (args.exe, args.src):
        if not os.path.exists(p):
            print("缺少 %s" % p)
            return 2

    blob, segs = load_pe(args.exe)
    print("镜像基址 0x400000，%d 个段：" % len(segs))
    for s in segs:
        note = ""
        if s["vs"] > s["rawsz"]:
            note = "  (VirtualSize 远大于 RawSize，%.1f MB 是 BSS)" % (
                (s["vs"] - s["rawsz"]) / 1048576.0)
        print("  %-10s RVA %#08x  vs %#010x  raw %#08x%s"
              % (s["name"], s["rva"], s["vs"], s["rawsz"], note))

    src = open(args.src, encoding="utf-8", errors="replace").read()

    # 抓 `exe + 0x...` 和 `#define XXX 0x...`（后者给数据指针用）
    pat_exe = re.compile(r"(?:\(BYTE \*\))?exe\s*\+\s*(0x[0-9A-Fa-f]+)")
    # 钩子安装函数把 RVA 当参数传：install_xxx_hook(exe, 0x45CE60)
    pat_arg = re.compile(r"install_\w+\s*\(\s*exe\s*,\s*(0x[0-9A-Fa-f]+)")
    # 只有名字像"地址"的 #define 才算地址；UI_OFF_* 这类是结构体偏移量
    pat_def = re.compile(r"^#define\s+(\w*(?:ROOT|RVA|ADDR|BASE)\w*)\s+(0x[0-9A-Fa-f]+)")

    hits = []
    for lineno, line in enumerate(src.splitlines(), 1):
        st = line.strip()
        if st.startswith("*") or st.startswith("/*") or st.startswith("//"):
            continue
        for m in pat_exe.finditer(line):
            hits.append((lineno, int(m.group(1), 16), st, "exe+"))
        for m in pat_arg.finditer(line):
            hits.append((lineno, int(m.group(1), 16), st, "exe+"))
        m = pat_def.match(st)
        if m:
            hits.append((lineno, int(m.group(2), 16), st, m.group(1)))

    print("\n扫描到 %d 处硬编码地址：" % len(hits))
    bad = 0
    for lineno, rva, text, kind in hits:
        sec = find_section(segs, rva)
        secname = sec["name"] if sec else "(不在任何段)"
        b = bytes_at(blob, segs, rva, 8)
        where = "%#08x %-8s" % (rva, secname)

        if kind == "exe+":
            # 代码地址：必须在白名单且落在 .text
            if rva not in KNOWN_CODE:
                print("  ✗ L%-5d %s 未登记在 KNOWN_CODE 白名单" % (lineno, where))
                print("         %s" % text[:100])
                bad += 1
                continue
            sig, label, cc = KNOWN_CODE[rva]
            if secname != ".text":
                print("  ✗ L%-5d %s %s 不在 .text，不能当代码跳转"
                      % (lineno, where, label))
                bad += 1
                continue
            real = bytes_at(blob, segs, rva, len(sig))
            if real != sig:
                print("  ✗ L%-5d %s %s 签名不符 期望 %s 实际 %s"
                      % (lineno, where, label, sig.hex(" "),
                         real.hex(" ") if real else "?"))
                bad += 1
                continue
            print("  ✓ L%-5d %s %-22s %-18s %s"
                  % (lineno, where, label, cc, b.hex(" ")))
        else:
            # 数据地址：必须登记，且段类型合理
            if rva not in KNOWN_DATA:
                print("  ✗ L%-5d %s %s 未登记在 KNOWN_DATA 白名单"
                      % (lineno, where, kind))
                bad += 1
                continue
            if secname not in WRITABLE:
                print("  ✗ L%-5d %s %s 期望可写段，实际 %s"
                      % (lineno, where, KNOWN_DATA[rva], secname))
                bad += 1
                continue
            print("  ✓ L%-5d %s %-22s %s"
                  % (lineno, where, KNOWN_DATA[rva],
                     "BSS(运行时零填充)" if b is None else b.hex(" ")))

    # 反向检查：白名单里有、但源码里没用到 —— 提示，不算失败
    used = {rva for _, rva, _, kind in hits if kind == "exe+"}
    unused = sorted(set(KNOWN_CODE) - used)
    if unused:
        print("\n提示：KNOWN_CODE 里登记但源码未引用（无害）：")
        for rva in unused:
            print("  %#08x  %s" % (rva, KNOWN_CODE[rva][1]))

    print()
    if bad:
        print("✗ %d 个问题" % bad)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
