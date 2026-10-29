#!/usr/bin/env python3
"""
WITE2 中文化 —— 一键构建脚本

用法:
  python scripts/build_all.py            完整构建（提取→翻译表→字体→部署）
  python scripts/build_all.py --diff     列出待翻译条目
  python scripts/build_all.py --check    检查部署状态
  python scripts/build_all.py --restore  还原游戏原始文件

游戏目录查找顺序:
  1. 环境变量 WITE2_DIR
  2. 仓库根目录下的 game_dir.txt
  3. Steam 默认库路径
"""
import csv, json, os, re, shutil, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")

EN_CSV = os.path.join(ROOT, "translation.english.csv")
DICT_JSON = os.path.join(ROOT, "zh_dict.json")
TSV = os.path.join(ROOT, "wite2_zh.tsv")

FONT_FILE = "wite2_zh.ttf"
FAMILY = "WITE2ZH"
SRC_TTC = r"C:\Windows\Fonts\msyh.ttc"          # 微软雅黑
PUNCT = "，。、；：？！（）《》〈〉「」『』【】〔〕—…·～％＋－×÷＝　"


# ------------------------------------------------------------------ 路径
def find_game():
    env = os.environ.get("WITE2_DIR")
    if env and os.path.isdir(env):
        return env

    cfg = os.path.join(ROOT, "game_dir.txt")
    if os.path.exists(cfg):
        p = open(cfg, encoding="utf-8").read().strip()
        if p and os.path.isdir(p):
            return p

    name = "Gary Grigsby's War in the East 2"
    guesses = []
    for drive in "CDEFGHIJ":
        guesses.append(os.path.join(f"{drive}:\\SteamLibrary\\steamapps\\common", name))
        guesses.append(os.path.join(f"{drive}:\\Program Files (x86)\\Steam\\steamapps\\common", name))
        guesses.append(os.path.join(f"{drive}:\\Steam\\steamapps\\common", name))
    for g in guesses:
        if os.path.exists(os.path.join(g, "WarInTheEast2.exe")):
            return g

    raise SystemExit(
        "找不到游戏目录。\n"
        "请设置环境变量 WITE2_DIR，或在仓库根目录创建 game_dir.txt 写入路径。"
    )


GAME = None
EXE = None


def init_paths():
    global GAME, EXE
    GAME = find_game()
    EXE = os.path.join(GAME, "WarInTheEast2.exe")
    print("游戏目录:", GAME)


# ------------------------------------------------------------------ 提取
def extract():
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

    rd = None
    for s in secs:
        if s[0] == ".rdata":
            rd = d[s[3]:s[3] + s[4]]
    if rd is None:
        raise SystemExit("找不到 .rdata")

    # 过滤第三方库 / 内部日志 / 文件路径 / 格式串
    BLACK = re.compile(r"""D3DFMT|D3DERR|D3DX|zlib|inflate|deflate|gzip|
HTTP|Accept-|Content-|Transfer-|User-Agent|Cache-Control|Authorization|
WinHttp|SSL|TLS|certificate|proxy|redirect|chunk|
json|JSON|UTF-8|UTF-16|iso-8859|
\.dll|\.exe|\.tga|\.bmp|\.wav|\.csv|\.dat|\.scn|\.jpg|\.pdf|\.wmv|\.txt|
%[sdzu]|\\\\|^\W+$""", re.X)

    items, seen = [], set()
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){2,}", rd):
        s = m.group().decode("utf-16le")
        if len(s) < 3 or len(s) > 200 or s in seen or BLACK.search(s):
            continue
        if not re.match(r"^[A-Za-z]", s):
            continue
        if not re.fullmatch(r"""[A-Za-z0-9 .,:;'&/()%+\-!?"\[\]_~<>|#*=]+""", s):
            continue
        seen.add(s)
        items.append(s)

    with open(EN_CSV, "w", encoding="utf-8", newline="") as f:
        f.write("ID,English Key,English Length, Description, max length, Translate?, Translated Text\r\n")
        for i, s in enumerate(items):
            f.write('%d,"%s",%d,,,Y,\r\n' % (i, s.replace('"', '""'), len(s)))
    print("提取 %d 条 -> translation.english.csv" % len(items))
    return items


# ------------------------------------------------------------------ 翻译表
def load_dict():
    zh = json.load(open(DICT_JSON, encoding="utf-8"))
    return {k: v for k, v in zh.items() if not k.startswith("_")}


def build_tsv(zh):
    lines = ["# WITE2 中英对照表  UTF-8  TAB 分隔", "# 英文<TAB>中文"]
    for k, v in zh.items():
        lines.append("%s\t%s" % (k, v))
    open(TSV, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print("翻译表 %d 条 -> wite2_zh.tsv" % len(zh))


# ------------------------------------------------------------------ 字体
def build_font(zh):
    from fontTools import subset
    from fontTools.ttLib import TTCollection, TTFont

    chars = {chr(c) for c in range(0x20, 0x7F)} | set(PUNCT)
    for v in zh.values():
        chars.update(c for c in v if ord(c) > 0x7F)

    if not os.path.exists(SRC_TTC):
        raise SystemExit("找不到源字体 %s（需要系统安装微软雅黑）" % SRC_TTC)

    coll = TTCollection(SRC_TTC, lazy=False)
    font = coll.fonts[0]
    cmap = font.getBestCmap()
    miss = [c for c in chars if ord(c) not in cmap]
    if miss:
        print("警告: 源字体缺 %d 字: %s" % (len(miss), "".join(miss[:20])))

    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.drop_tables += ["DSIG"]
    sub = subset.Subsetter(options=opts)
    sub.populate(text="".join(sorted(chars)))
    sub.subset(font)

    # 改成唯一 family name，防止引擎加载系统里的完整字体
    for rec in list(font["name"].names):
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID == 4:
            rec.string = FAMILY + " Regular"
        elif rec.nameID == 6:
            rec.string = FAMILY + "-Regular"
        elif rec.nameID == 3:
            rec.string = FAMILY + ";v1.0"

    font_dir = os.path.join(GAME, "Dat", "Fonts")
    os.makedirs(font_dir, exist_ok=True)
    out = os.path.join(font_dir, FONT_FILE)
    font.save(out)

    n = len(TTFont(out).getBestCmap())
    print("字体 %d 字形 -> %s (%.0f KB)" % (n, out, os.path.getsize(out) / 1024))


def patch_fonts_json():
    p = os.path.join(GAME, "Dat", "GuiFonts.json")
    bak = os.path.join(ROOT, "_backup", "GuiFonts.json")
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    if not os.path.exists(bak):
        shutil.copy2(p, bak)
        print("备份 GuiFonts.json")

    data = json.load(open(p, encoding="utf-8"))
    cnt = 0
    for k, v in data.items():
        if isinstance(v, dict) and v.get("id"):
            v["name"] = FAMILY
            v["file"] = FONT_FILE
            cnt += 1
    json.dump(data, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("GuiFonts.json: %d 个字体 -> %s" % (cnt, FAMILY))


# ------------------------------------------------------------------ 部署
def deploy():
    dll = os.path.join(BUILD, "version.dll")
    if not os.path.exists(dll):
        print("警告: build/version.dll 不存在，先运行 build.bat")
    else:
        shutil.copy2(dll, os.path.join(GAME, "version.dll"))
        print("部署 -> version.dll")
    shutil.copy2(TSV, os.path.join(GAME, "wite2_zh.tsv"))
    print("部署 -> wite2_zh.tsv")


def restore():
    for f in ("version.dll", "wite2_zh.tsv", "wite2_cfg.ini", "wite2_hook.txt"):
        p = os.path.join(GAME, f)
        if os.path.exists(p):
            os.remove(p)
            print("删除", f)

    bak = os.path.join(ROOT, "_backup", "GuiFonts.json")
    if os.path.exists(bak):
        shutil.copy2(bak, os.path.join(GAME, "Dat", "GuiFonts.json"))
        print("还原 GuiFonts.json")

    bak2 = os.path.join(ROOT, "_backup", "FontConfig.txt")
    if os.path.exists(bak2):
        shutil.copy2(bak2, os.path.join(GAME, "Dat", "FontConfig.txt"))
        print("还原 FontConfig.txt")

    fp = os.path.join(GAME, "Dat", "Fonts", FONT_FILE)
    if os.path.exists(fp):
        os.remove(fp)
        print("删除", FONT_FILE)


# ------------------------------------------------------------------ diff / check
def diff():
    if not os.path.exists(EN_CSV):
        print("还没有 translation.english.csv，先运行完整构建")
        return
    zh = load_dict()

    keys = []
    with open(EN_CSV, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) >= 2:
                keys.append(row[1])

    todo = [k for k in keys if k not in zh]
    done = len(keys) - len(todo)
    print("英文总数: %d" % len(keys))
    print("已翻译  : %d (%.1f%%)" % (done, 100 * done / max(1, len(keys))))
    print("待翻译  : %d" % len(todo))

    # 译文过长的（无法原地改写）
    tooLong = [k for k, v in zh.items() if len(v) > len(k)]
    if tooLong:
        print("\n译文过长(会被跳过) %d 条:" % len(tooLong))
        for k in tooLong[:20]:
            print("   %r -> %r" % (k, zh[k]))

    if todo:
        out = os.path.join(ROOT, "todo.txt")
        open(out, "w", encoding="utf-8").write("\n".join(todo))
        print("\n待翻列表 -> todo.txt")


def check():
    for f in ("version.dll", "wite2_zh.tsv"):
        p = os.path.join(GAME, f)
        print("  %-16s %s" % (f, "OK" if os.path.exists(p) else "缺失"))

    fp = os.path.join(GAME, "Dat", "Fonts", FONT_FILE)
    if os.path.exists(fp):
        from fontTools.ttLib import TTFont
        n = len(TTFont(fp).getBestCmap())
        print("  %-16s OK (%d 字形)" % (FONT_FILE, n))
    else:
        print("  %-16s 缺失" % FONT_FILE)

    log = os.path.join(GAME, "wite2_hook.txt")
    if os.path.exists(log):
        txt = open(log, encoding="utf-8", errors="replace").read()
        print("\n--- wite2_hook.txt ---")
        print(txt.strip())


if __name__ == "__main__":
    init_paths()
    if "--restore" in sys.argv:
        restore()
    elif "--diff" in sys.argv:
        diff()
    elif "--check" in sys.argv:
        check()
    else:
        extract()
        zh = load_dict()
        build_tsv(zh)
        build_font(zh)
        patch_fonts_json()
        deploy()
        print()
        diff()
