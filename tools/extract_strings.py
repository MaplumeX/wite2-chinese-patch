#!/usr/bin/env python3
"""
WITE2 界面文本提取器
用法: python extract_strings.py <游戏目录>
输出: translation.english.csv  (ID, English Key, English Length, Description, max length, Translate?, Translated Text)

游戏更新后重跑此脚本，与旧文件 diff 即可得到新增/变更条目，只需补翻增量。
"""
import struct, re, sys, os

GAME = sys.argv[1] if len(sys.argv) > 1 else r"G:\SteamLibrary\steamapps\common\Gary Grigsby's War in the East 2"
EXE = os.path.join(GAME, "WarInTheEast2.exe")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translation.english.csv")

d = open(EXE, "rb").read()
e = struct.unpack_from("<I", d, 0x3C)[0]
nsec = struct.unpack_from("<H", d, e + 6)[0]
optsz = struct.unpack_from("<H", d, e + 20)[0]
secs = []
for i in range(nsec):
    o = e + 24 + optsz + 40 * i
    name = d[o:o + 8].rstrip(b"\0").decode()
    vs, va, rs, ra = struct.unpack_from("<IIII", d, o + 8)
    secs.append((name, va, vs, ra, rs))

def section(name):
    for s in secs:
        if s[0] == name:
            return d[s[3]:s[3] + s[4]]
    raise SystemExit("section %s not found" % name)

rd = section(".rdata")

# 界面文本在 .rdata 里是 UTF-16LE 明文
pat = re.compile(rb"(?:[\x20-\x7e]\x00){2,}")

# 过滤第三方库 / 技术串（HTTP 头、JSON 库、zlib、D3D、文件路径、格式串）
BLACK = re.compile(r"""D3DFMT|D3DERR|D3DX|zlib|inflate|deflate|gzip|
HTTP|Accept-|Content-|Transfer-|User-Agent|Cache-Control|Authorization|
WinHttp|SSL|TLS|certificate|proxy|redirect|chunk|
json|JSON|UTF-8|UTF-16|iso-8859|
\.dll|\.exe|\.tga|\.bmp|\.wav|\.csv|\.dat|\.scn|\.jpg|\.pdf|\.wmv|\.txt|
%[sdzu]|\\\\|^\W+$""", re.X)

items, seen = [], set()
for m in pat.finditer(rd):
    s = m.group().decode("utf-16le")
    if len(s) < 3 or len(s) > 200 or s in seen:
        continue
    if BLACK.search(s):
        continue
    if not re.match(r"^[A-Za-z]", s):
        continue
    if not re.fullmatch(r"""[A-Za-z0-9 .,:;'&/()%+\-!?"\[\]_~<>|#*=]+""", s):
        continue
    seen.add(s)
    items.append(s)

# 按在 .rdata 中的出现顺序编号（与游戏内部顺序一致，ID 稳定）
with open(OUT, "w", encoding="utf-8", newline="") as f:
    f.write("ID,English Key,English Length, Description, max length, Translate?, Translated Text\r\n")
    for i, s in enumerate(items):
        f.write('%d,"%s",%d,,,Y,\r\n' % (i, s.replace('"', '""'), len(s)))

print("提取 %d 条 -> %s" % (len(items), OUT))
