/*
 * WITE2 中文化 v4.1 —— 纯内存改写 .rdata 字符串（零运行时开销）
 * ============================================================
 *
 * 演进过程:
 *   v1-v3  hook DrawText 绘制函数
 *          → 主菜单标签成功, 但工具提示走另一条绘制路径, 没翻译
 *          → 游戏里至少有三个文本绘制函数 (DrawText / DrawTextScaled /
 *            Render3DText), 逐个逆向定位代价高且容易漏
 *
 *   v4    改为直接改写内存里的字符串
 *          → 任何代码路径读到的都是中文, 不管它用哪个绘制函数
 *          → 调用方测量宽度时读的也是中文, 排版自动正确
 *
 *   v4.1  移除 v4 里那个 DrawText 兜底 hook
 *          → 它每次绘制都调 VirtualQuery 检查可读性, 每字符一次,
 *            一帧几千次系统调用, 导致明显掉帧
 *          → 实测兜底 hook 零命中 (.rdata 改写已覆盖全部文本),
 *            纯属白跑, 直接删掉
 *          → 现在运行时零开销: 只在启动时改一次内存
 *
 * 原理:
 *   所有界面文本都以 UTF-16 明文存放在 .rdata 中, 且是只读常量。
 *   启动时把英文原地覆盖成中文。中文在 UTF-16 下远比英文短
 *   ("Game Options" 26 字节 vs "游戏选项" 10 字节), 空间充足。
 *
 * 安全约束:
 *   1. 只在译文不超长时改写 (len(zh) <= len(en)), 绝不越界
 *   2. 只匹配完整字符串 (前后都是 NUL 边界),
 *      避免把 "Show Game Options screen" 里的 "Game Options" 当成独立串
 *   3. 只改 .rdata (只读常量区), 不碰 .data 里的运行时状态
 *
 * 抗更新: 只改运行时内存, 磁盘上的 exe 不动。
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
/* IMAGE_IMPORT_DESCRIPTOR / IMAGE_THUNK_DATA：窗口头里已有，
 * 但 IMAGE_DIRECTORY_ENTRY_IMPORT 的两个常量在某些 SDK 配置下仍需确认 */
#ifndef IMAGE_DIRECTORY_ENTRY_IMPORT
#define IMAGE_DIRECTORY_ENTRY_IMPORT 1
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

/* gzip 解压 / 打包（src/inflate.c）。
 * 自己写而不是用 zlib：DLL 只依赖 KERNEL32，而 SDK 里没有 zlib。 */
int gz_decompress(const unsigned char *in, size_t inlen,
                  unsigned char **out, size_t *outlen);
int gz_compress_stored(const unsigned char *in, size_t inlen,
                       unsigned char **out, size_t *outlen);

/* .dat 数据层的纯逻辑（解密/解压/查表/替换/打包），可离线测试 */
#include "dat_layer.h"


/* ================= version.dll 转发 ================= */
static HMODULE g_sysVersion = NULL;
static FARPROC sysproc(const char *name)
{
    if (!g_sysVersion) {
        g_sysVersion = LoadLibraryA("C:\\Windows\\System32\\version.dll");
        if (!g_sysVersion) return NULL;
    }
    return GetProcAddress(g_sysVersion, name);
}
#define FWD(ret, name, args, callargs)                  \
    ret WINAPI fwd_##name args                          \
    {                                                   \
        typedef ret (WINAPI *fn_t) args;                \
        static fn_t fn = NULL;                          \
        if (!fn) fn = (fn_t)sysproc(#name);             \
        if (!fn) return (ret)0;                         \
        return fn callargs;                             \
    }
FWD(BOOL,  GetFileVersionInfoA,        (LPCSTR a, DWORD b, DWORD c, LPVOID d), (a,b,c,d))
FWD(BOOL,  GetFileVersionInfoW,        (LPCWSTR a, DWORD b, DWORD c, LPVOID d), (a,b,c,d))
FWD(DWORD, GetFileVersionInfoSizeA,    (LPCSTR a, LPDWORD b), (a,b))
FWD(DWORD, GetFileVersionInfoSizeW,    (LPCWSTR a, LPDWORD b), (a,b))
FWD(BOOL,  GetFileVersionInfoExA,      (DWORD f, LPCSTR a, DWORD b, DWORD c, LPVOID d), (f,a,b,c,d))
FWD(BOOL,  GetFileVersionInfoExW,      (DWORD f, LPCWSTR a, DWORD b, DWORD c, LPVOID d), (f,a,b,c,d))
FWD(DWORD, GetFileVersionInfoSizeExA,  (DWORD f, LPCSTR a, LPDWORD b), (f,a,b))
FWD(DWORD, GetFileVersionInfoSizeExW,  (DWORD f, LPCWSTR a, LPDWORD b), (f,a,b))
FWD(BOOL,  GetFileVersionInfoByHandle, (DWORD f, HANDLE h, DWORD a, DWORD b, LPVOID c), (f,h,a,b,c))
FWD(BOOL,  VerQueryValueA,             (LPCVOID a, LPCSTR b, LPVOID *c, PUINT d), (a,b,c,d))
FWD(BOOL,  VerQueryValueW,             (LPCVOID a, LPCWSTR b, LPVOID *c, PUINT d), (a,b,c,d))
FWD(DWORD, VerFindFileA,               (DWORD a, LPCSTR b, LPCSTR c, LPCSTR d, LPSTR e, PUINT f, LPSTR g, PUINT h), (a,b,c,d,e,f,g,h))
FWD(DWORD, VerFindFileW,               (DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPWSTR e, PUINT f, LPWSTR g, PUINT h), (a,b,c,d,e,f,g,h))
FWD(DWORD, VerInstallFileA,            (DWORD a, LPCSTR b, LPCSTR c, LPSTR d, LPSTR e, PUINT f), (a,b,c,d,e,f))
FWD(DWORD, VerInstallFileW,            (DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPWSTR e, PUINT f), (a,b,c,d,e,f))
FWD(UINT,  VerLanguageNameA,           (DWORD a, LPSTR b, UINT c), (a,b,c))
FWD(UINT,  VerLanguageNameW,           (DWORD a, LPWSTR b, UINT c), (a,b,c))

/* ================= 词典 ================= */
#define MAXENT 65536
/* sec: 0 = .rdata 和 .data 都改（默认）
 *      1 = 只改 .rdata
 *      2 = 只改 .data
 *
 * 为什么需要这个: 工厂/资源类型名（PORT、RAILYARD、OIL、FUEL、MANPOWER…）
 * 在 .data 里是定长显示数组（步长 0x28 = 20 个 wchar），但在 .rdata 里
 * 另有同名的枚举副本。改 .data 的显示数组没问题，改 .rdata 的枚举
 * 会重演 WATER/RIVER 那次事故——地图渲染拿它做查找，水体变黑。 */
typedef struct { wchar_t *key; wchar_t *val; int sec; } Pair;
static Pair g_dic[MAXENT];
static int  g_ndic = 0;
static int  g_nDataOnly = 0;

/* sec == 5 是旧版封印标记。部队番号翻译已移除，构建不再生成这类行；
 * dict_load 仍会跳过它们，以兼容旧版 TSV。 */

static wchar_t *u8w(const char *s, int n)
{
    int c = MultiByteToWideChar(CP_UTF8, 0, s, n, NULL, 0);
    if (c <= 0) return NULL;
    wchar_t *w = (wchar_t *)malloc((c + 1) * sizeof(wchar_t));
    if (!w) return NULL;
    MultiByteToWideChar(CP_UTF8, 0, s, n, w, c); w[c] = 0; return w;
}

static void dict_load(void)
{
    FILE *f = fopen("wite2_zh.tsv", "rb");
    if (!f) return;
    char line[8192];
    char un[8192];
    while (fgets(line, sizeof(line), f) && g_ndic < MAXENT) {
        /* 行首标记
         *   '#' 注释行
         *   '@' 转义行：后跟"转义过的 key + 真 TAB + 译文"。
         *       用于 key 本身以 '#' 开头（否则会被当注释丢掉），
         *       或 key 内含 TAB（否则会把列分隔弄乱）的条目。
         *       转义规则：\\t = TAB，\\\\ = 反斜杠。
         * 实例：`### ERROR : game failed to save ###` 和
         * 带制表符对齐的选项说明段，都只能走 '@' 行。 */
        if (line[0] == '#') continue;

        const char *src = line;
        int esc = 0;
        if (line[0] == '@') { src = line + 1; esc = 1; }

        char *t = strchr((char *)src, '\t');
        if (!t) continue;
        *t = 0;
        char *en = (char *)src, *zh = t + 1;

        if (esc) {
            /* 反转义 key（只动 key，译文另处理） */
            int o = 0;
            for (char *p = en; *p && o < (int)sizeof(un) - 2; p++) {
                if (*p == '\\' && p[1]) {
                    p++;
                    if (*p == 't') un[o++] = '\t';
                    else if (*p == 'n') un[o++] = '\n';
                    else if (*p == '\\') un[o++] = '\\';
                    else { un[o++] = '\\'; un[o++] = *p; }
                } else un[o++] = *p;
            }
            un[o] = 0;
            en = un;
            /* 译文也要反转义：那几段选项说明的译文保留了制表符对齐 */
            int o2 = 0;
            char unzh[8192];
            for (char *p = zh; *p && o2 < (int)sizeof(unzh) - 2; p++) {
                if (*p == '\\' && p[1]) {
                    p++;
                    if (*p == 't') unzh[o2++] = '\t';
                    else if (*p == 'n') unzh[o2++] = '\n';
                    else if (*p == '\\') unzh[o2++] = '\\';
                    else { unzh[o2++] = '\\'; unzh[o2++] = *p; }
                } else unzh[o2++] = *p;
            }
            unzh[o2] = 0;
            zh = unzh;
        }

        size_t zl = strlen(zh);
        while (zl && (zh[zl-1] == '\n' || zh[zl-1] == '\r')) zh[--zl] = 0;

        /* 可选第三列: 作用域标记
         *   d = 只改 .data（天气/气候/飞机数据列这类只在 .data 出现的词）
         *   r = 只改 .rdata
         *   t = 只在绘制层翻译，不改内存
         *   空 = .rdata 和 .data 都改
         * 不剥掉这一列，译文会带上 "\td"，界面上显示成“大雨d”。 */
        int sec = 0;
        char *sp = strchr(zh, '\t');
        if (sp) {
            *sp = 0;
            zl = strlen(zh);
            if (sp[1] == 'd') sec = 2;
            else if (sp[1] == 'r') sec = 1;
            else if (sp[1] == 'b') sec = 3;   /* 两段都改 */
            else if (sp[1] == 't') sec = 4;   /* 只在绘制层翻，不改内存 */
            else if (sp[1] == 'x') sec = 5;   /* 旧版封印标记，跳过不加载 */
        }

        /* 旧版 TSV 的封印行（x）：部队番号翻译已移除，现在直接丢弃。 */
        if (sec == 5) continue;

        size_t el = strlen(en);
        if (!el || !zl) continue;
        wchar_t *k = u8w(en, (int)el), *v = u8w(zh, (int)zl);
        if (!k || !v) { free(k); free(v); continue; }
        g_dic[g_ndic].key = k;
        g_dic[g_ndic].val = v;
        g_dic[g_ndic].sec = sec;
        if (sec == 2) g_nDataOnly++;
        g_ndic++;
    }
    fclose(f);
}

/* ================= 日志 ================= */
static FILE *g_log = NULL;
static int   g_logTried = 0;
static void logfmt(const char *fmt, ...)
{
    if (!g_logTried) { g_logTried = 1; g_log = fopen("wite2_hook.txt", "wb"); }
    if (!g_log) return;
    va_list ap; va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fflush(g_log);
}

/* ================= .rdata 原地改写 ================= */
static int g_patched = 0, g_tooLong = 0, g_notFound = 0;

/* 改写开关。游戏目录放对应的 ini 文件就能关掉一段，用来二分定位崩溃。
 *
 * 为什么需要
 * ----------
 * 绘制层换指针永远不会弄崩游戏 —— 它只影响这一次绘制用什么字符串。
 * 崩一定来自 .rdata / .data 的原地改写：游戏用那些串做查找键，
 * 改成中文后查不到，拿到 NULL 再解引用。
 *
 * 实例：Message 改成"消息"，点"通讯报告"必崩。它在 exe 里紧挨着
 * ErrorCode / DebugMessage / Password / Login /
 * authentication/steam/login/anonymous，是 Steam 登录响应的 JSON
 * 字段名表。short_class() 只拦 6 字符以内的串，7 字符刚好吃漏。
 *
 *   wite2_nopatch.ini  两段都不改（纯绘制层）
 *   wite2_nordata.ini  不改 .rdata
 *   wite2_nodata.ini   不改 .data
 *
 * 改写明细写到 wite2_patch.txt，崩溃后能对照到底哪条被改了。 */
static int g_patchRdata = 1;
static int g_drawHook = 1;        /* 0 = 不装绘制层 hook */
/* 二分用：只改写 .rdata 的 [g_rLo, g_rHi) 偏移区间。 */
static DWORD g_rLo = 0;
static DWORD g_rHi = 0xFFFFFFFF;
static int g_patchData = 1;
static FILE *g_patLog = NULL;

/* 收集 buf 里所有完整 UTF-16 字符串匹配的偏移 */
static int wfind_all(const BYTE *buf, DWORD size, const wchar_t *key, int klen,
                     DWORD *outs, int maxOut)
{
    int n = 0;
    DWORD need = (DWORD)(klen * sizeof(wchar_t));
    if (size < need + sizeof(wchar_t)) return 0;
    const BYTE *kb = (const BYTE *)key;
    DWORD last = size - need - sizeof(wchar_t);
    for (DWORD i = 0; i <= last; i += 2) {
        if (i > 0 && *(const wchar_t *)(buf + i - 2) != 0) continue;
        if (memcmp(buf + i, kb, need) != 0) continue;
        if (*(const wchar_t *)(buf + i + need) != 0) continue;
        outs[n++] = i;
        if (n >= maxOut) return n;
        i += need - 2;   /* 跳过已匹配部分 */
    }
    return n;
}

static void patch_section(BYTE *base, DWORD size, const char *secName, int secId)
{
    if (!size) return;

    DWORD oldProt;
    if (!VirtualProtect(base, size, PAGE_READWRITE, &oldProt)) {
        logfmt("[patch] %s VirtualProtect 失败\n", secName);
        return;
    }

    DWORD *offs = (DWORD *)malloc(sizeof(DWORD) * 256);
    int cnt = 0;
    for (int d = 0; d < g_ndic; d++) {
        /* secId: 1 = .rdata, 2 = .data
         *
         * .data 只处理显式标记为 dataOnly 的词。否则 5000+ 个普通词会
         * 把 356 KB 的段各扫一遍（合计 2 GB），启动明显变慢，
         * 而它们本来就不在 .data 里。
         *
         * .rdata 跳过 dataOnly 词：这些词在 .rdata 有同名枚举副本，
         * 不能碰。 */
        /* sec 4 = 只在绘制层翻译，两个段都不改。
         * 短串（<=6 字符）在 .rdata 几乎都是枚举、ISO 语言码、
         * CSV 列名、BBCode 标记，改了会破坏游戏逻辑。例如
         *   "no"  是 ISO 639 挪威语代码（ja|ko|nl|no|pl）
         *   "de"  是德语代码
         *   "CP"  是 Command Points
         *   "SP"  是西班牙国家码（AM|VC|IQ|SP|SZ）
         * 这些串在界面上出现的概率极低，却一改就出事。
         * 绘制层已经能翻译它们，不必动内存。 */
        if (g_dic[d].sec == 4) continue;
        /* sec 5 = 封印，两个段都不动。 */
        if (g_dic[d].sec == 5) continue;

        /* sec 3 = 两段都改。给"在 .data 是显示数组、在 .rdata 只是
         * 安全界面标签"的词用，例如难度名 NORMAL。 */
        if (secId == 2) {
            if (g_dic[d].sec != 2 && g_dic[d].sec != 3) continue;
        } else {
            if (g_dic[d].sec == 2) continue;
        }
        const wchar_t *k = g_dic[d].key;
        const wchar_t *v = g_dic[d].val;
        int klen = (int)wcslen(k);
        int vlen = (int)wcslen(v);

        if (vlen > klen) { g_tooLong++; continue; }

        int n = wfind_all(base, size, k, klen, offs, 256);
        if (n == 0) { g_notFound++; continue; }

        for (int j = 0; j < n; j++) {
            /* 只替换**完整**的 NUL 结尾字符串。
             * 自由子串替换会把 "Climate: Humid" 里的 "Climate: "
             * 换成 "气候：" 留下 "气候：Humid"。 */
            DWORD off = offs[j];
            wchar_t *p = (wchar_t *)(base + off);
            if (off >= sizeof(wchar_t)) {
                wchar_t prev = *(wchar_t *)(base + off - sizeof(wchar_t));
                if (prev != 0) continue;
            }
            if (off + (DWORD)(klen + 1) * sizeof(wchar_t) <= size) {
                wchar_t next = *(wchar_t *)(base + off + klen * sizeof(wchar_t));
                if (next != 0) continue;
            }
            /* 只写译文，紧跟着就放终止符，**不补空格**。
             *
             * 这里曾经把译文补空格到原文长度，理由是"绘制时空格
             * 不产生宽度"。那个假设是错的，而且正是界面错位的根源。
             * 实测（wite2_layout.txt，游戏选项面板右对齐到 x=471）：
             *
             *   4 汉字的标签 x=423，5 汉字的标签 x=411  ->  12 px/字
             *   旧版不补空格：x=423，可见文字右边缘 = 423 + 48 = 471 ✓
             *   现在补 8 个空格：x=395，含空格右边缘 = 471，但可见文字
             *   右边缘只有 395 + 48 = 443，整整偏左 28 px。
             *
             * 游戏是拿 wcslen（含尾随空格）算宽度的，空格 3.55 px 一个，
             * 会实打实占位。补得越多，可见文字越往左跑。左对齐的列表
             * 则表现为右边留一大块空白——这就是"汉化后没对齐"。
             *
             * 不补空格不会越界：终止符提前，写在原字符串内部，后面
             * 的字段一个字节都不动。旧版本就是这么做的，没出过事。 */
            /* 二分用：只改区间内的串。 */
            if (secId == 1 && (off < g_rLo || off >= g_rHi)) continue;
            {
                int q;
                for (q = 0; q < vlen; q++) p[q] = v[q];
                p[vlen] = 0;
            }
            cnt++;
            g_patched++;
            /* 明细入日志：崩溃后靠它对照哪个串被改错了。
             * 只记前 4000 条，避免日志比扫描还慢。 */
            if (g_patLog && g_patched <= 4000) {
                /* k 是 wchar_t*，不能当 char* 遍历 —— 曾经这么写过，
                 * 遇到第一个 0x00 就停，日志里每个 key 都只剩首字符
                 * （"Army Group Center" 显示成 "A"）。差点根据那份
                 * 错日志得出一堆错误结论。 */
                char kb[600], vb[600];
                int x = 0;
                for (const wchar_t *q = k; *q && x < 500; q++) {
                    if ((unsigned)*q < 0x7f) kb[x++] = (char)*q;
                    else x += _snprintf(kb + x, 8, "\\u%04X",
                                        (unsigned)*q);
                }
                kb[x] = 0;
                x = 0;
                for (const wchar_t *q = v; *q && x < 500; q++) {
                    if ((unsigned)*q < 0x7f) vb[x++] = (char)*q;
                    else x += _snprintf(vb + x, 8, "\\u%04X",
                                        (unsigned)*q);
                }
                vb[x] = 0;
                fprintf(g_patLog, "%s@%08X\t%s\t%s\n",
                        secName, (unsigned)off, kb, vb);
            }
        }
    }
    free(offs);
    VirtualProtect(base, size, oldProt, &oldProt);
    logfmt("[patch] %s: 改写 %d 处\n", secName, cnt);
}

static void patch_all(HMODULE exe)
{
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)exe;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((BYTE *)exe + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;

    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        char nm[9] = {0};
        memcpy(nm, sec->Name, 8);
        /* .rdata: 界面文本
         * .data : 天气/气候名称等运行时初始化的字符串表
         *         （Clear / Rain / Heavy Rain / Snowfall / Blizzard
         *          以及 Temperate Humid 这类气候带名都在这里） */
        int secId = 0;
        if (strcmp(nm, ".rdata") == 0) secId = 1;
        else if (strcmp(nm, ".data") == 0) secId = 2;
        else continue;

        /* 二分开关：关掉某段就完全不碰它。 */
        if (secId == 1 && !g_patchRdata) {
            logfmt("[patch] %s 已禁用（wite2_nordata.ini）\n", nm);
            continue;
        }
        if (secId == 2 && !g_patchData) {
            logfmt("[patch] %s 已禁用（wite2_nodata.ini）\n", nm);
            continue;
        }

        /* 必须取 VirtualSize 和 SizeOfRawData 的较小值。
         * .data 的 VirtualSize 是 0x5AF1FAC（90 MB），因为未初始化的
         * BSS 合并在里面；而真实初始化数据只有 0x56E00（356 KB）。
         * 拿 VirtualSize 当扫描长度会让 5619 个词各扫 90 MB
         * （合计 500 GB），启动直接卡死，日志停在 .rdata 那一行。 */
        DWORD span = sec->Misc.VirtualSize;
        if (sec->SizeOfRawData && sec->SizeOfRawData < span)
            span = sec->SizeOfRawData;
        patch_section((BYTE *)exe + sec->VirtualAddress, span, nm, secId);
    }
}

/* ================= 阶段2: 在绘制层替换文本指针 =================
 *
 * 五轮失败记录:
 *   0x45EB80 (fread)      改 .dat 缓冲 -> 显示源是后来的 UTF-16 副本
 *   MultiByteToWideChar   部队名不走它 (日志里全是文件路径)
 *   0x1B3EB0 (逐字节扩宽) 只被调用 7 次, len 468~2MB, 不是部队名
 *   扫描堆 + 原地改写      崩溃 x2
 *      原因1: 筛选放过 PAGE_READONLY, memcpy 写只读页
 *      原因2 (根本): 游戏内部用字符串做查找/比较, 内容一改查找失败 -> 空指针
 *            而且扫描与游戏分配并发, 可能写进已释放的堆块
 *
 * 结论: 绝不能在数据层改字符串。只能在"绘制"这一刻换指针。
 *
 * 绘制函数 0x45CE60 (__thiscall, ret 0x14):
 *   反汇编证据 (0x45CF20):
 *     mov  ebx, dword ptr [esp + 0x50]   ; ebx = wchar_t* 文本 (第4个栈参数)
 *     movzx eax, word ptr [ebx]          ; 读第一个宽字符
 *     lea  edx, [edx + 2]                ; 逐宽字符遍历
 *   序言: 83 EC 38 56 8B F1 = sub esp,0x38 / push esi / mov esi,ecx  (正好 6 字节)
 *
 * 栈布局 (入口时): [esp]=返回地址 [esp+4]=arg1 [esp+8]=arg2 [esp+0xC]=arg3
 *                  [esp+0x10]=文本指针  [esp+0x14]=flags
 *
 * hook 做法:
 *   序言 6 字节换成 E9 跳转, 原 6 字节放进 trampoline。
 *   钩子里把栈上的文本指针换成我们的缓冲区, 然后跳到 trampoline。
 *   不改任何游戏数据, 只在这一次调用里换指针 -> 零副作用。
 */

/* 绘制层词典容量。
 * 曾经是 8192，但词典涨到 9758 条后，dict_build 里
 * "g_nzh < MAXZH" 会静默丢掉最后 1500 多条 —— 不在日志里说，
 * 界面上就是"有些名字明明翻了却还是英文"。留一倍余量。 */
#define MAXZH 32768
typedef struct { char *key; int klen; wchar_t *wtext; int wlen; int cs; } ZhEntry;
static ZhEntry g_zh[MAXZH];
static int g_nzh = 0;

static void  *g_dtTramp = NULL;
static volatile LONG g_dtCalls = 0;
static volatile LONG g_dtHits = 0;
static int g_logBudget = 0;

/* 布局诊断开关（见 dt_check2） */
static int g_layoutLog = 0;

/* ===== 全量捕获 =====
 *
 * 之前只能靠用户报告"哪个名字没翻"，然后我按报告去猜字符串形态。
 * 猜错的代价很高：XXXXVI Mot. Cr 这一条修了三轮才修对，因为每一轮
 * 我建模的形态都和真实显示不一致。
 *
 * 这里把绘制层遇到的**每一个字符串**都记下来（去重），游戏目录放一个
 * 空的 wite2_capture.ini 就开启。跑几分钟浏览部队列表，就能拿到
 * 完整清单，然后离线验证，不用再猜。
 */
static int g_capture = 0;
#define CAP_N 40000
static unsigned *g_capHash = NULL;
static FILE *g_capFile = NULL;
/* 绘制 hook 在调用 dt_check 前填入的调用点信息。
 * g_curRet  = 调用者返回地址（反查是哪个界面画的）
 * g_curThis = __thiscall 的 this（区分同类界面的不同实例）
 * 渲染是单线程的，直接存全局即可，不进栈参数。 */
static DWORD g_curRet = 0;
static DWORD g_curThis = 0;
/* 绘制 hook 入口时的 ebp / esp，用来沿帧链回溯调用栈。
 * 只看返回地址没用 —— 游戏只有一个文本绘制入口，所有界面的
 * 返回地址都一样。必须往上翻几层才能区分是哪个面板画出来的。 */
static DWORD g_curEbp = 0;
static DWORD g_curEsp = 0;
#define BT_DEPTH 6
static DWORD g_bt[BT_DEPTH];
static volatile LONG g_capCount = 0;

/* ===== 区域采集 =====
 *
 * 为什么需要: 3 帧栈链撑不起"这是哪个界面"。
 *
 * 0x4449A0 是文本排版/自动换行（入口 mov eax,0x1810; call _chkstk，
 * 逐字符处理，上限 0x600），所有文本都从它流到 0x45CE60。它的唯一
 * 调用点是 0x4450F9，位于 0x445020（列表项绘制）内。于是 hook 抓到的
 * 3 帧全部落在"公共出口 -> 公共入口"之间，界面帧在更上面，而更上面
 * 已经被 0xC0C0C0 这类栈填充值污染。实测 380 条捕获里，最粗的一条链
 * 独占 186 条，横跨主菜单/剧本列表/部队详情/地图浮窗/编辑器。
 *
 * 可靠信号在控件管理器里（0x445020 和 0x4466D0 反复使用同一套寻址）:
 *
 *   ui   = exe + 0xE72800      (文件态 0x1272800，运行时被重定位)
 *   idx  = [ui + 0x200]        当前控件索引
 *   cap  = [ui + 0x21C]        控件总数
 *   arr  = [ui + 0x218]        控件数组，元素 0x30 字节
 *   elem = arr + idx*0x30
 *   txt  = [elem + 0x1C]       文本子对象
 *   ctl  = [elem + 0x20]       控件本体，[ctl] 是 vtable
 *
 * vtable 是精确地址匹配，假阳性几乎为零。.rdata 里有 542 个
 * vtable-like 块，其中 17 个 40 槽的块很可能是 UI 控件类。控件类型
 * （按钮/列表行/表头/输入框）比"哪个界面"更有翻译价值: 按钮要压到
 * 2-4 字，表头压到 4-6 字，提示可以长。
 *
 * 第一版只读不写。偏移是从 0x445020 静态推出来的，运行时索引时机
 * 可能不同，读失败就记 0 / -1，绝不影响游戏。
 */
#define UI_ROOT_RVA   0x0E72800
#define UI_OFF_INDEX  0x200
#define UI_OFF_CAP    0x21C
#define UI_OFF_ARR    0x218
#define UI_ELEM_SIZE  0x30
#define UI_ELEM_TEXT  0x1C
#define UI_ELEM_CTL   0x20
#define REG_N         200000
#define REG_HASH      262144

static DWORD g_exeBase = 0;
static int   g_regions = 0;
static FILE *g_regFile = NULL;
static unsigned *g_regSeen = NULL;
static volatile LONG g_regCount = 0;
static int   g_regProbed = 0;
/* 控件管理器的实际地址。优先用栈上找到的那个 —— 0x1272800 这个
 * 常量地址读出来字段全是 0，说明它不是真正的实例。 */
static DWORD g_uimgr = 0;

/* (text, this, x, y) 的哈希。同一帧里同一串会被重画几十次，
 * 不去重的话 20 万行上限几秒就打满，后面的界面一条都记不进来。
 * 实测一次完整浏览产生 20 万行原始记录，去重后只有 4 千条。 */
static unsigned reg_hash(const wchar_t *t, int len, float x, float y,
                         DWORD th)
{
    unsigned h = 2166136261u;
    for (int i = 0; i < len; i++) { h ^= (unsigned)t[i]; h *= 16777619u; }
    h ^= th;                      h *= 16777619u;
    h ^= (unsigned)(int)(x * 4.0f); h *= 16777619u;
    h ^= (unsigned)(int)(y * 4.0f); h *= 16777619u;
    return h ? h : 1u;
}

static int reg_seen(const wchar_t *t, int len, float x, float y, DWORD th)
{
    unsigned h, start;
    if (!g_regSeen) return 0;
    h = reg_hash(t, len, x, y, th);
    start = h % REG_HASH;
    for (unsigned i = 0; i < REG_HASH; i++) {
        unsigned idx = (start + i) % REG_HASH;
        if (g_regSeen[idx] == 0) { g_regSeen[idx] = h; return 0; }
        if (g_regSeen[idx] == h) return 1;
    }
    return 1;
}

/* 读一个 DWORD，失败返回 0。不把异常抛给调用方。 */
static DWORD ui_rd(DWORD addr)
{
    DWORD v = 0;
    if (!addr || (addr & 3)) return 0;
    __try { v = *(volatile DWORD *)addr; }
    __except (EXCEPTION_EXECUTE_HANDLER) { v = 0; }
    return v;
}

/* 从栈上找控件管理器。
 *
 * 判据是 0x445020 里那三个字段同时成立:
 *   [p+0x200] 当前索引   [p+0x21C] 总数   [p+0x218] 数组指针
 * 三个字段同时落在这个范围内的概率极低。
 *
 * 0x445020 的 this 在 ecx，但它的栈帧里保存了 esi（=this），
 * 所以从绘制入口往上扫 4KB 应该能扫到。 */
static DWORD find_uimgr(DWORD *stk)
{
    DWORD found = 0;
    __try {
        for (int i = 0; i < 1024; i++) {
            DWORD p = stk[i];
            DWORD arr;
            int cap, idx;
            if (p < 0x10000 || p > 0x7FFFFFFF || (p & 3)) continue;
            arr = ui_rd(p + UI_OFF_ARR);
            cap = (int)ui_rd(p + UI_OFF_CAP);
            idx = (int)ui_rd(p + UI_OFF_INDEX);
            if (arr < 0x10000 || arr > 0x7FFFFFFF) continue;
            if (cap <= 0 || cap > 10000) continue;
            if (idx < 0 || idx >= cap) continue;
            if (ui_rd(arr + UI_ELEM_CTL) == 0 &&
                ui_rd(arr + UI_ELEM_TEXT) == 0) continue;
            found = p;
            break;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        /* 扫到栈顶了，保留已找到的 */
    }
    return found;
}

/* 宽串 -> 可读 UTF-8，非 ASCII 用 <XXXX> 转义，和 capture 一个格式。 */
static int region_enc(char *out, int room, const wchar_t *s)
{
    int o = 0;
    if (!s || room <= 12) return 0;
    for (int i = 0; s[i] && i < 200; i++) {
        wchar_t c = s[i];
        if (c >= 0x20 && c < 0x7f) {
            if (o >= room - 1) break;
            out[o++] = (char)c;
        } else {
            int w;
            if (o >= room - 12) break;
            w = _snprintf(out + o, room - o, "<%04X>", (unsigned)c);
            if (w <= 0) break;
            o += w;
        }
    }
    return o;
}

/* 每次绘制写一行。不去重 —— 同一个文本在不同控件上要分别统计，
 * 这正是上下文翻译要的数据。 */
static void region_write(const wchar_t *text, const wchar_t *dst, DWORD *stk)
{
    if (!g_regions || !g_regFile || !text || !stk) return;

    {
        int len = 0;
        while (len < 200 && text[len]) len++;
        if (len < 2) return;
        if (reg_seen(text, len, *(float *)&stk[1], *(float *)&stk[2],
                     g_curThis)) return;
    }
    if (InterlockedIncrement(&g_regCount) > REG_N) return;

    DWORD this_ = g_curThis, this_vt = 0, font = 0;
    DWORD ui = g_uimgr;
    int idx = -1, capn = -1;
    DWORD arr = 0, elem = 0, ctl = 0, ctl_vt = 0, txtobj = 0;

    /* 常量地址读出来字段全 0，那就从栈上找真正的实例。只找一次。 */
    if (!ui) {
        ui = find_uimgr(stk);
        if (ui) {
            g_uimgr = ui;
            logfmt("[regions] uimgr=%08X（栈上找到；常量地址 %08X 字段全 0）\n",
                   (unsigned)ui, (unsigned)(g_exeBase + UI_ROOT_RVA));
        }
    }
    if (!ui) ui = g_exeBase ? g_exeBase + UI_ROOT_RVA : 0;

    if (ui) {
        idx  = (int)ui_rd(ui + UI_OFF_INDEX);
        capn = (int)ui_rd(ui + UI_OFF_CAP);
        arr  = ui_rd(ui + UI_OFF_ARR);
        if (arr && idx >= 0 && idx < capn && capn < 100000) {
            elem   = arr + (DWORD)idx * UI_ELEM_SIZE;
            txtobj = ui_rd(elem + UI_ELEM_TEXT);
            ctl    = ui_rd(elem + UI_ELEM_CTL);
            if (ctl) ctl_vt = ui_rd(ctl);
        }
    }
    if (this_) {
        this_vt = ui_rd(this_);
        font    = ui_rd(this_ + 0xb8);
    }

    /* 第一次读到像样的控件信息时记一笔，方便判断偏移推得对不对。
     * 全 0 说明 ui 指针或字段偏移不成立。 */
    if (!g_regProbed) {
        g_regProbed = 1;
        logfmt("[regions] ui=%08X [ui]=%08X [ui+200]=%08X "
               "[ui+218]=%08X [ui+21C]=%08X idx=%d cap=%d "
               "ctl=%08X ctl_vt=%08X this=%08X [this]=%08X\n",
               (unsigned)ui, (unsigned)ui_rd(ui),
               (unsigned)ui_rd(ui + UI_OFF_INDEX),
               (unsigned)ui_rd(ui + UI_OFF_ARR),
               (unsigned)ui_rd(ui + UI_OFF_CAP),
               idx, capn, (unsigned)ctl, (unsigned)ctl_vt,
               (unsigned)this_, (unsigned)this_vt);
    }

    /* 把 elem 结构体前 0x30 字节（12 个 DWORD）整块记下来。
     *
     * 上一次采集发现 +0x20（控件本体）全是 0，而 +0x1C（文本子对象）
     * 非 0 —— 说明绘制走的是 0x44508F 分支，this 就是文本子对象，
     * 不是控件。元素结构里剩下的字段还没看过，类型/矩形/ID 很可能
     * 就在里面。整块记下来，离线对比哪些字段对同一面板恒定、
     * 哪些随控件变化，就能定位。 */
    {
        char elembuf[168];
        int eo = 0;
        elembuf[0] = 0;
        if (elem) {
            for (int k = 0; k < 12; k++) {
                int wr = _snprintf(elembuf + eo, 16, "%08X,",
                                   (unsigned)ui_rd(elem + (DWORD)k * 4));
                if (wr <= 0) break;
                eo += wr;
            }
            if (eo > 0) elembuf[eo - 1] = 0;
        }

        {
            char line[1800];
            int o = 0, w;
            w = _snprintf(line, sizeof(line),
                          "%08X\t%08X\t%08X\t%d\t%d\t%08X\t%08X\t%08X\t"
                          "%.1f\t%.1f\t%d\t%d\t%08X\t",
                          (unsigned)g_curRet, (unsigned)this_,
                          (unsigned)this_vt,
                          idx, capn, (unsigned)txtobj, (unsigned)ctl,
                          (unsigned)ctl_vt,
                          *(float *)&stk[1], *(float *)&stk[2],
                          (int)stk[3], (int)stk[5], (unsigned)font);
            if (w <= 0) return;
            o = w;
            o += region_enc(line + o, (int)sizeof(line) - o - 300, text);
            line[o++] = '\t';
            o += region_enc(line + o, (int)sizeof(line) - o - 60, dst);
            line[o++] = '\t';
            w = _snprintf(line + o, (int)sizeof(line) - o - 2, "%s", elembuf);
            if (w > 0) o += w;
            line[o++] = '\n';
            fwrite(line, 1, o, g_regFile);
            if ((g_regCount & 0xFF) == 0) fflush(g_regFile);
        }
    }
}

static unsigned cap_hash(const wchar_t *t, int len)
{
    unsigned h = 2166136261u;
    for (int i = 0; i < len; i++) {
        h ^= (unsigned)t[i];
        h *= 16777619u;
    }
    return h ? h : 1u;
}

/* 已记录过就返回 1 */
static int cap_seen(const wchar_t *t, int len)
{
    unsigned h = cap_hash(t, len);
    unsigned start = h % CAP_N;
    for (unsigned i = 0; i < CAP_N; i++) {
        unsigned idx = (start + i) % CAP_N;
        if (g_capHash[idx] == 0) { g_capHash[idx] = h; return 0; }
        if (g_capHash[idx] == h) return 1;
    }
    return 1;
}

/* 右边缘判据已废弃：补丁时机改到 DllMain 后不再需要坐标修正。
 * 保留这段注释是为了说明为什么这里空了，而不是被误删。 */

static int g_layoutCount = 0;

/* 替换用的缓冲池: 每次调用占一个槽, 调用返回后才复用。
 *
 * 32 -> 256 的原因：指挥官报告、OOB 树这类界面一帧内要画几百个
 * 文本。游戏不是画一个丢一个，它会先拿到一批指针再统一绘制。
 * 池子只有 32 个槽，第 33 个字符串就把第 1 个的缓冲覆盖了 ——
 * 游戏手里的指针仍指在池内，但内容已经被换成别的（往往更长）串。
 * 如果它按旧长度算好缓冲再拷，就是越界写。
 *
 * 256 个槽 = 128 KB 静态，代价可以忽略。 */
#define POOL 256
static wchar_t g_pool[POOL][256];
static volatile LONG g_poolIdx = 0;

/* ===== 格式串模板匹配 =====
 *
 * 游戏在绘制前会先 sprintf 再交给绘制函数，所以绘制层看到的永远是
 * 实例化后的文本：
 *
 *   词典里      绘制层看到
 *   HQ Type: %d   ->  "HQ Type: 3"
 *   %d HQs        ->  "12 HQs"
 *   Turn %d  %s   ->  "Turn 5   xxx"
 *
 * 整串精确匹配永远命不中这些。所以把带 % 的 key 单独存一份，
 * 用占位符当通配符来匹配。
 */
typedef struct { int idx; } FmtIdx;
static int g_fmt[MAXZH];
static int g_nfmt = 0;

static wchar_t lc_w(wchar_t c);   /* 定义在下面，这里先声明 */

/* 占位符: % 后面跟可选修饰符，最后是转换字符 */
static int is_conv(wchar_t c)
{
    return c == L'd' || c == L'i' || c == L'u' || c == L's' ||
           c == L'f' || c == L'x' || c == L'X' || c == L'c' || c == L'%';
}

/* 匹配一个占位符，返回消耗的字符数；0 = 不匹配。
 * out/outlen 用于把实际匹配到的文本回写（可能为 NULL）。 */
static int match_ph(const wchar_t *t, int len, wchar_t conv,
                    const wchar_t **out, int *outlen)
{
    if (len <= 0) return 0;
    int n = 0;
    if (conv == L'd' || conv == L'i' || conv == L'u' || conv == L'x' ||
        conv == L'X') {
        if (t[0] == L'-' || t[0] == L'+') n = 1;
        int d = n;
        while (n < len && t[n] >= L'0' && t[n] <= L'9') n++;
        if (n == d) return 0;              /* 至少要有一位数字 */
        if (conv == L'x' || conv == L'X') {
            while (n < len && ((t[n] >= L'0' && t[n] <= L'9') ||
                   (t[n] >= L'a' && t[n] <= L'f') ||
                   (t[n] >= L'A' && t[n] <= L'F'))) n++;
        }
    } else if (conv == L's') {
        /* 贪婪，调用者靠后续字面量回退 */
        n = len;
    } else if (conv == L'c') {
        n = 1;
    } else {
        return 0;
    }
    if (out) { *out = t; *outlen = n; }
    return n;
}

/* 模板能把出的最短文本长度。
 *
 * 为什么需要
 * ----------
 * 格式串循环前面本来有个剪枝 `if (e->klen > len) continue;`，
 * 理由“模板至少要比实际串短”。这个假设是错的：
 *
 *     key  "%d turns"   klen = 8
 *     text "3 turns"    len  = 7          ← 8 > 7，被剪掉
 *
 * `%d` 占 2 个字符，但最小只输出 1 位数字。于是**所有单数字**都翻不了：
 * `Turn 5`、`3 HQs`、`3 turns`、`%d x %s` 里的 1 位数……
 * 实测：`12 turns` 命中、`3 turns` 不命中，就是这个剪枝造成的。
 *
 * 正确做法是按占位符的最小宽度算，而不是拿 key 长度当估计。
 */
static int fmt_minlen(const char *key)
{
    int n = 0;
    for (int i = 0; key[i]; ) {
        if (key[i] != '%') { n++; i++; continue; }
        int j = i + 1;
        while (key[j] && !is_conv((wchar_t)(unsigned char)key[j])) j++;
        if (!key[j]) { n++; i++; continue; }   /* 裸 %，当字面量 */
        char conv = key[j];
        /* 解析宽度: flags(-+ #0) 后、小数点前的数字 */
        int width = 0, k = i + 1;
        while (key[k] && strchr("-+ #0", key[k])) k++;
        while (key[k] >= '0' && key[k] <= '9') {
            width = width * 10 + (key[k] - '0');
            k++;
        }
        if (conv == '%') {
            n += 1;
        } else if (width > 0) {
            n += width;                        /* %20s / %3d 至少有宽度那么长 */
        } else if (conv == 's') {
            n += 0;                            /* 空串是合法值 */
        } else {
            n += 1;                            /* %d/%u/%x/%c 至少一位 */
        }
        i = j + 1;
    }
    return n;
}


/* 精确查词（整串、大小写不敏感）。给格式串的 %s 回填用：
 * "Difficulty: %s" + "IMPOSSIBLE" 需要把 IMPOSSIBLE 也翻掉，
 * 否则会得到「难度：IMPOSSIBLE」这种半中半英。 */
static int lookup_exact(const wchar_t *s, int len, wchar_t *out, int cap)
{
    for (int i = 0; i < g_nzh; i++) {
        if (g_zh[i].klen != len) continue;
        int ok = 1;
        for (int z = 0; z < len; z++) {
            wchar_t a = s[z];
            wchar_t b = (wchar_t)(unsigned char)g_zh[i].key[z];
            if (g_zh[i].cs) { if (a != b) { ok = 0; break; } }
            else if (lc_w(a) != lc_w(b)) { ok = 0; break; }
        }
        if (!ok) continue;
        if (g_zh[i].wlen + 1 > cap) return 0;
        memcpy(out, g_zh[i].wtext, (g_zh[i].wlen + 1) * sizeof(wchar_t));
        return 1;
    }
    return 0;
}

/* 拿 key 和 text 做模板匹配。命中则把译文写进 out（含占位符回填）。 */
static int fmt_match(const char *key, const wchar_t *val,
                     const wchar_t *text, int len, wchar_t *out, int cap)
{
    /* 收集占位符实际匹配到的文本 */
    const wchar_t *vals[16];
    int vlens[16];
    int nvals = 0;

    const wchar_t *t = text;
    int tl = len;
    int ki = 0;

    while (key[ki]) {
        if (key[ki] != L'%') {
            if (tl <= 0) return 0;
            if (lc_w(*t) != lc_w((wchar_t)(unsigned char)key[ki])) return 0;
            t++; tl--; ki++;
            continue;
        }
        /* 解析占位符: %[flags][width][.prec]conv */
        int k2 = ki + 1;
        while (key[k2] && !is_conv(key[k2])) k2++;
        if (!key[k2]) return 0;
        wchar_t conv = key[k2];
        ki = k2 + 1;
        if (conv == L'%') {
            if (tl <= 0 || *t != L'%') return 0;
            t++; tl--;
            continue;
        }
        if (nvals >= 16) return 0;

        const wchar_t *got = NULL;
        int gotlen = 0;
        if (conv == L's') {
            /* 找下一个字面量在 text 中的位置 */
            int next_lit = ki;
            while (key[next_lit] && key[next_lit] != L'%') next_lit++;
            int litlen = next_lit - ki;
            int take = -1;
            if (litlen == 0) {
                take = tl;                  /* 末尾 %s 吃掉剩余 */
            } else {
                for (int p = 0; p + litlen <= tl; p++) {
                    int ok = 1;
                    for (int z = 0; z < litlen; z++)
                        if (lc_w(t[p + z]) !=
                            lc_w((wchar_t)(unsigned char)key[ki + z])) { ok = 0; break; }
                    if (ok) { take = p; break; }
                }
                if (take < 0) return 0;
            }
            got = t; gotlen = take;
            t += take; tl -= take;
        } else {
            int adv = match_ph(t, tl, conv, &got, &gotlen);
            if (!adv) return 0;
            t += adv; tl -= adv;
        }
        vals[nvals] = got; vlens[nvals] = gotlen; nvals++;
    }

    if (tl != 0) return 0;                  /* text 必须刚好用完 */

    /* 回填译文 */
    int o = 0, vi = 0;
    for (int j = 0; val[j]; j++) {
        if (val[j] != L'%') {
            if (o + 1 >= cap) return 0;
            out[o++] = val[j];
            continue;
        }
        int j2 = j + 1;
        while (val[j2] && !is_conv(val[j2])) j2++;
        if (!val[j2]) return 0;
        if (val[j2] == L'%') {
            if (o + 1 >= cap) return 0;
            out[o++] = L'%';
            j = j2;
            continue;
        }
        if (vi >= nvals) return 0;
        /* %s 的内容再查一次词典: "Difficulty: %s" + "IMPOSSIBLE"
         * 要得到「难度：不可能」，而不是「难度：IMPOSSIBLE」。 */
        wchar_t sub[256];
        int used = 0;
        if (val[j2] == L's' && vlens[vi] > 0 &&
            lookup_exact(vals[vi], vlens[vi], sub, 256)) {
            for (int z = 0; sub[z]; z++) {
                if (o + 1 >= cap) return 0;
                out[o++] = sub[z];
            }
            used = 1;
        }
        if (!used) {
            for (int z = 0; z < vlens[vi]; z++) {
                if (o + 1 >= cap) return 0;
                out[o++] = vals[vi][z];
            }
        }
        vi++;
        j = j2;
    }
    out[o] = 0;
    return 1;
}

/* 大小写敏感词表: 首字母大写是显示标签, 全大写是资源/枚举名 */
static int is_cs_key(const char *k)
{
    static const char *cs[] = {
        "Port", "Rail", "Road", "City", "Town", "Airfield", "Coast",
        "River", "Water", "Swamp", "Forest", "Woods", "Mountain",
        "Urban", "Desert", "Tundra", "Sand", "Border", "Clear", "Rough",
        "Major", "Minor", "None", "Snow", "Mud",
    };
    for (int i = 0; i < (int)(sizeof(cs)/sizeof(cs[0])); i++)
        if (strcmp(k, cs[i]) == 0) return 1;
    return 0;
}

static void dict_build(void)
{
    for (int d = 0; d < g_ndic && g_nzh < MAXZH; d++) {
        const wchar_t *kw = g_dic[d].key;
        const wchar_t *vw = g_dic[d].val;

        char key[256];
        int klen = 0, ok = 1;
        for (int j = 0; kw[j]; j++) {
            /* 上限放到 0xFF：地名里有重音字符（Bátaszék 的 á 是
             * U+00E1）。曾经卡在 0x7e，这些条目被静默丢掉，
             * 界面上就一直是原文。 */
            if (kw[j] > 0xff || klen >= 255) { ok = 0; break; }
            key[klen++] = (char)kw[j];
        }
        /* 2 字符词条也要收。曾经门槛是 3，结果 "Cr"（军）、"Bn"（营）
         * 这类缩写根本进不了绘制层词典 —— "XXXXVI Mot.Cr" 修了三轮都
         * 修不好，就是因为它命中的是 klen==2 的条目，而这里静默丢了。
         * 绘制层的匹配是按整词长度精确比对，短 key 不会误伤长串。
         * 1 字符也要收：方位词 N/S/E/W 就是单字母。 */
        if (!ok || klen < 1) continue;
        /* key[] 是栈上数组，循环退出时没有写终止符。
         * 不补这个 0，下面 memcpy(kc, key, klen+1) 会把栈上的
         * 未初始化字节当终止符复制走，日志里就会出现
         * "OctM"、"Difficulty: NORMALr"、"Road to Karelia...乱码"。
         * 匹配本身不受影响（用 klen 单独记长度），但这是未定义行为。 */
        key[klen] = 0;

        int wlen = 0;
        while (vw[wlen]) wlen++;
        if (wlen < 1 || wlen > 250) continue;

        char *kc = (char *)malloc(klen + 1);
        wchar_t *wc = (wchar_t *)malloc((wlen + 1) * sizeof(wchar_t));
        if (!kc || !wc) { free(kc); free(wc); continue; }
        memcpy(kc, key, klen + 1);
        memcpy(wc, vw, (wlen + 1) * sizeof(wchar_t));

        g_zh[g_nzh].key = kc;
        g_zh[g_nzh].klen = klen;
        g_zh[g_nzh].wtext = wc;
        g_zh[g_nzh].wlen = wlen;
        /* 地形/资源词必须大小写敏感。
         * .rdata 里 "Port"(显示标签) 和 "PORT"(资源枚举) 是两个不同的串，
         * 前者该翻成"港口"，后者用于查找 //Tiles//PORT// 下的图形，
         * 翻了会让地图渲染出错。 */
        g_zh[g_nzh].cs = is_cs_key(kc);
        g_nzh++;
    }
    if (g_nzh >= MAXZH)
        logfmt("[zh] 警告: 词典超过 MAXZH(%d)，后面的条目被丢弃\n", MAXZH);

    /* 按 key 长度降序: 保证 "Army Group Center" 先于 "Army" 命中,
     * 避免短词抢走长词的匹配机会 */
    for (int a = 1; a < g_nzh; a++) {
        ZhEntry t = g_zh[a];
        int b = a - 1;
        while (b >= 0 && g_zh[b].klen < t.klen) {
            g_zh[b + 1] = g_zh[b];
            b--;
        }
        g_zh[b + 1] = t;
    }

    /* 排序后再建格式串索引，保持长 key 优先 */
    for (int i = 0; i < g_nzh; i++) {
        if (strchr(g_zh[i].key, '%'))
            if (g_nfmt < MAXZH) g_fmt[g_nfmt++] = i;
    }

    logfmt("[zh] %d entries ready (%d format templates)\n",
           g_nzh, g_nfmt);
}

/* 大小写不敏感比较: 游戏有些串在 .rdata 里是大写开头, 绘制时变小写,
 * 例如 "Show Production screen" 对 "Show Production Screen"。
 * 只处理 ASCII, 词典 key 都是 ASCII。 */
static wchar_t lc_w(wchar_t c)
{
    if (c >= L'A' && c <= L'Z') return (wchar_t)(c - L'A' + L'a');
    return c;
}

/* 词字符: 字母数字。子串匹配必须在词边界上, 否则 "Start" 会吃掉
 * "Starting" 的前半截, 变成 "开始ing"。 */
static int is_word_ch(wchar_t c)
{
    return (c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z') ||
           (c >= L'0' && c <= L'9');
}

/* ================= 文本收集器 =================
 * 绘制函数是唯一的"真实显示"入口, 所以这里收集到的字符串
 * 就是游戏实际画出来的文本 —— 比从 CSV 猜准确得多。
 * 哈希表去重, 后台线程每 5 秒全量写盘。
 */
#define CH_MAX 16384
static wchar_t *g_chKeys[CH_MAX];
static volatile LONG g_chCount = 0;
static int g_collect = 0;

static unsigned hash_w(const wchar_t *s, int len)
{
    unsigned h = 2166136261u;
    for (int i = 0; i < len; i++) {
        h ^= (unsigned)s[i];
        h *= 16777619u;
    }
    return h;
}

static void collect_add(const wchar_t *s, int len)
{
    if (!g_collect || len < 2 || len > 400) return;

    unsigned h = hash_w(s, len) & (CH_MAX - 1);
    for (int probe = 0; probe < 48; probe++) {
        unsigned idx = (h + probe) & (CH_MAX - 1);
        wchar_t *e = g_chKeys[idx];
        if (!e) {
            wchar_t *cp = (wchar_t *)malloc((len + 1) * sizeof(wchar_t));
            if (!cp) return;
            memcpy(cp, s, len * sizeof(wchar_t));
            cp[len] = 0;
            if (InterlockedCompareExchange((volatile LONG *)&g_chKeys[idx],
                                           (LONG)(LONG_PTR)cp, 0) == 0) {
                InterlockedIncrement(&g_chCount);
            } else {
                free(cp);
            }
            return;
        }
        int m = 1;
        for (int z = 0; z <= len; z++) {
            if (e[z] != s[z]) { m = 0; break; }
        }
        if (m) return;
    }
}

static DWORD WINAPI collect_writer(LPVOID p)
{
    for (;;) {
        Sleep(5000);
        FILE *f = fopen("wite2_strings.txt", "wb");
        if (!f) continue;
        fwrite("\xEF\xBB\xBF", 1, 3, f);
        int n = 0;
        for (int i = 0; i < CH_MAX; i++) {
            wchar_t *e = g_chKeys[i];
            if (!e) continue;
            char buf[1600];
            int c = WideCharToMultiByte(65001, 0, e, -1, buf, sizeof(buf), NULL, NULL);
            if (c > 1) fwrite(buf, 1, c - 1, f);
            fwrite("\r\n", 1, 2, f);
            n++;
        }
        fclose(f);
        logfmt("[collect] %d strings written\n", n);
    }
    return 0;
}


/* 检查绘制文本, 命中则返回替换后的指针
 *
 * 设计原则: 绝不自由拼接。
 *
 * 之前用"子串替换 + 宽松前缀匹配"导致大量碎片:
 *   "Show Production screen" -> "Show 生产 screen"
 *   "National"               -> "国家al"
 *   "Army Grp. Sout"         -> "集团军 Grp. Sout"
 * 根因是任何短 key 只要出现在长串里就会被替换。
 *
 * 现在的策略, 从严格到宽松:
 *   1   整串精确匹配 (大小写不敏感)     <- 最安全, 覆盖绝大多数
 *   1.5 剥离尾部空格后精确匹配         <- 界面用空格填充对齐
 *   1.6 剥离文件扩展名后精确匹配       <- "1941 Campaign.scn"
 *   1.4 格式串模板匹配                 <- "Max TOE %"
 *   2   剥离尾部后缀后精确匹配         <- 表格列头整串绘制
 *
 * 部队番号（"113th ID"、"Army Group Center"、截断/缩写形式）
 * 一律原样返回，不做任何拼装。
 */
/* ===== 宽度表: 让译文占位宽度和原文一致 =====
 *
 * 为什么需要: 游戏先算好绘制坐标再调绘制函数。
 *
 *   x = 中心 - 原文宽度/2        （居中）
 *   x = 右边 - 原文宽度          （右对齐）
 *
 * 我在绘制这一刻把英文换成中文，但 x 已经按英文宽度算定了。
 * 中文窄得多（"Fog of War" 5.33 em -> 战争迷雾 4.0 em），
 * 从同一个 x 开始画，视觉中心就落到左边:
 *
 *   目标中心 530，x=452，中文 4em -> 视觉中心 452+65 = 517，偏左 13px
 *
 * 修法: 给译文补尾随空格凑够原文宽度。空格不显形，但占位宽度参与
 * 居中计算，所以三种对齐方式都能修正。左对齐时补空格无副作用。
 */
static const unsigned char g_asciiW[95] = {
    76, 80, 112, 163, 150, 228, 223, 66, 86, 86, 116, 190, 62, 111, 62, 109,
    150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 62, 62, 190, 190, 190, 124,
    255, 180, 161, 171, 195, 141, 136, 190, 198, 75, 101, 162, 131, 250, 208, 209,
    157, 209, 167, 148, 147, 191, 173, 255, 165, 154, 159, 86, 106, 86, 190, 115,
    76, 142, 164, 128, 164, 145, 89, 164, 158, 68, 68, 139, 68, 240, 158, 163,
    164, 164, 98, 118, 95, 158, 134, 202, 130, 136, 126, 86, 69, 86, 190,
};

/* 单个字符宽度，单位 1/256 em */
static int char_w(wchar_t c)
{
    if (c >= 0x20 && c < 0x7F) return g_asciiW[c - 0x20];
    /* CJK 及全角标点: 标准全宽 */
    if (c >= 0x2E80) return 256;
    return 128;
}

static int text_w(const wchar_t *s)
{
    int t = 0;
    for (int i = 0; s[i]; i++) t += char_w(s[i]);
    return t;
}

/* 把 rep 用尾随空格补齐到 orig 的宽度，写进 dst。
 * 只补不裁: 中文比原文窄才需要补；比原文宽就不动（宁可不居中也不截断）。 */
static void pad_to_width(const wchar_t *rep, const wchar_t *orig,
                         wchar_t *dst, int cap)
{
    int i = 0;
    for (; rep[i] && i < cap - 1; i++) dst[i] = rep[i];
    dst[i] = 0;

    int wo = text_w(orig), wr = text_w(rep);
    if (wr >= wo) return;

    const int sp = g_asciiW[0];          /* 空格宽度 */
    int need = (wo - wr) / sp;
    if (need <= 0) return;
    if (i + need >= cap - 1) need = cap - 2 - i;
    for (int k = 0; k < need; k++) dst[i + k] = L' ';
    dst[i + need] = 0;
}


static wchar_t *dt_check_body(wchar_t *text)
{
    if (!text) return text;
    InterlockedIncrement(&g_dtCalls);

    int len = 0;
    while (len < 300 && text[len]) len++;
    if (len < 2) return text;

    collect_add(text, len);

    int slot;


    /* ---------- 策略 1: 整串精确匹配 ---------- */
    for (int i = 0; i < g_nzh; i++) {
        if (g_zh[i].klen != len) continue;
        int ok = 1;
        for (int z = 0; z < len; z++) {
            wchar_t a = text[z];
            wchar_t b = (wchar_t)(unsigned char)g_zh[i].key[z];
            if (g_zh[i].cs) {
                if (a != b) { ok = 0; break; }
            } else {
                if (lc_w(a) != lc_w(b)) { ok = 0; break; }
            }
        }
        if (!ok) continue;

        slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
        memcpy(g_pool[slot], g_zh[i].wtext,
               (g_zh[i].wlen + 1) * sizeof(wchar_t));
        LONG n = InterlockedIncrement(&g_dtHits);
        if (n <= 40)
            logfmt("[dt-exact] \"%s\" -> %d wchars\n", g_zh[i].key, g_zh[i].wlen);
        return g_pool[slot];
    }

    /* ---------- 策略 1.5: 剥离尾部空格后精确匹配 ----------
     * 界面用空格填充对齐，绘制出来的串常带尾部空格：
     *   "1941 Campaign "   (剧本名从 .scn 文件名派生，列表里补空格)
     *   "Turn 5      "
     * 不剥尾部空格会完全失配，表现为“有的翻了有的没翻”。
     * 译文后面补回同样多的空格，保持原有对齐。 */
    {
        int tn = len;
        while (tn > 0 && text[tn - 1] == L' ') tn--;
        if (tn > 0 && tn < len) {
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen != tn) continue;
                int ok = 1;
                for (int z = 0; z < tn; z++) {
                    wchar_t a = text[z];
                    wchar_t b = (wchar_t)(unsigned char)g_zh[i].key[z];
                    if (g_zh[i].cs) {
                        if (a != b) { ok = 0; break; }
                    } else {
                        if (lc_w(a) != lc_w(b)) { ok = 0; break; }
                    }
                }
                if (!ok) continue;

                int pad = len - tn;
                if (g_zh[i].wlen + pad + 1 > 250) break;
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                wchar_t *out = g_pool[slot];
                memcpy(out, g_zh[i].wtext, g_zh[i].wlen * sizeof(wchar_t));
                for (int z = 0; z < pad; z++) out[g_zh[i].wlen + z] = L' ';
                out[g_zh[i].wlen + pad] = 0;
                InterlockedIncrement(&g_dtHits);
                return out;
            }
        }
    }

    /* ---------- 策略 1.55: 剥离前导/尾随空格后精确匹配 ----------
     * 列表和表格用前导空格做缩进，绘制出来的是 " MAP"、" Transfer"。
     * 整串匹配要求长度相等，策略 1.5 只剥尾部，于是这类串全部漏翻。
     * 实测一次界面浏览就有 225 处（MAP 82、Manual 27、transfer 22、
     * Western Europe 15、Axis Reserves 11……）。
     * 译文前后补回同样多的空格，缩进和对齐都不变。 */
    {
        int lead = 0, tn, trail;
        while (lead < len && text[lead] == L' ') lead++;
        tn = len - lead;
        while (tn > 0 && text[lead + tn - 1] == L' ') tn--;
        trail = len - lead - tn;
        if ((lead || trail) && tn > 0) {
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen != tn) continue;
                int ok = 1;
                for (int z = 0; z < tn; z++) {
                    wchar_t a = text[lead + z];
                    wchar_t b = (wchar_t)(unsigned char)g_zh[i].key[z];
                    if (g_zh[i].cs) {
                        if (a != b) { ok = 0; break; }
                    } else {
                        if (lc_w(a) != lc_w(b)) { ok = 0; break; }
                    }
                }
                if (!ok) continue;

                if (lead + g_zh[i].wlen + trail + 1 > 250) break;
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                {
                    wchar_t *out = g_pool[slot];
                    int o = 0;
                    for (int z = 0; z < lead; z++) out[o++] = L' ';
                    memcpy(out + o, g_zh[i].wtext,
                           g_zh[i].wlen * sizeof(wchar_t));
                    o += g_zh[i].wlen;
                    for (int z = 0; z < trail; z++) out[o++] = L' ';
                    out[o] = 0;
                    InterlockedIncrement(&g_dtHits);
                    return out;
                }
            }
        }
    }

    /* ---------- 策略 1.6: 剥离文件扩展名后精确匹配 ----------
     * 剧本名从 .scn 文件名派生，某些界面会把扩展名一起画出来：
     *   "1941 Campaign.scn" -> "1941 Campaign"
     */
    {
        static const char *exts[] = {
            ".scn", ".dat", ".txt", ".csv", ".sav", ".psv", ".ini"
        };
        for (int e = 0; e < (int)(sizeof(exts) / sizeof(exts[0])); e++) {
            int el = (int)strlen(exts[e]);
            if (len <= el) continue;
            int ok = 1;
            for (int z = 0; z < el; z++) {
                if (lc_w(text[len - el + z]) !=
                    lc_w((wchar_t)(unsigned char)exts[e][z])) { ok = 0; break; }
            }
            if (!ok) continue;

            int plen = len - el;
            while (plen > 0 && text[plen - 1] == L' ') plen--;
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen != plen) continue;
                int m = 1;
                for (int z = 0; z < plen; z++) {
                    wchar_t a = text[z];
                    wchar_t b = (wchar_t)(unsigned char)g_zh[i].key[z];
                    if (g_zh[i].cs) {
                        if (a != b) { m = 0; break; }
                    } else {
                        if (lc_w(a) != lc_w(b)) { m = 0; break; }
                    }
                }
                if (!m) continue;
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                memcpy(g_pool[slot], g_zh[i].wtext,
                       (g_zh[i].wlen + 1) * sizeof(wchar_t));
                InterlockedIncrement(&g_dtHits);
                return g_pool[slot];
            }
            break;
        }
    }

    /* ---------- 策略 1.4: 格式串模板匹配 ----------
     *
     * 游戏绘制前会先 sprintf，所以绘制层看到的是实例化后的文本：
     *   "HQ Type: %d"   ->  "HQ Type: 3"
     *   "%d HQs"        ->  "12 HQs"
     * 整串精确匹配命不中，但占位符当通配符就能。
     * 放在精确匹配之后、其余策略之前：字面量最多，误匹配风险最小。 */
    for (int f = 0; f < g_nfmt; f++) {
        ZhEntry *e = &g_zh[g_fmt[f]];
        /* 剪枝: 用模板能吐出的最短长度，不能用 key 长度
         * （%d 占 2 字符但只输出 1 位数字，见 fmt_minlen）。 */
        if (fmt_minlen(e->key) > len) continue;
        slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
        if (!fmt_match(e->key, e->wtext, text, len, g_pool[slot], 250))
            continue;
        /* 输出和原文一模一样就跳过。像 "%d,%d" 这种模板
         * 会把 "12,34" 原样吐回来，占着槽位还拖慢后续匹配。 */
        int same = 1;
        for (int z = 0; z <= len; z++) {
            if (g_pool[slot][z] != text[z]) { same = 0; break; }
        }
        if (same) continue;
        LONG n = InterlockedIncrement(&g_dtHits);
        if (n <= 40)
            logfmt("[dt-fmt] \"%s\" -> %d wchars\n", e->key, e->wlen);
        return g_pool[slot];
    }



    /* ---------- 策略 2: 剥离尾部后缀后精确匹配 ----------
     * 界面表格常把一列画成一整串，列之间用连续空格分隔：
     *   "PRODUCED     CONSUMED  AVAIL"
     * 只翻能整串命中的前缀，尾巴原样保留。
     * 只在明确的分隔处切: 空格+括号 / 连续两个以上空格 */
    {
        int cut = -1;
        for (int z = 0; z + 1 < len; z++) {
            /* 连续两个空格 */
            if (text[z] == L' ' && text[z + 1] == L' ') { cut = z; break; }
            /* 空格后跟括号 */
            if (text[z] == L' ' && text[z + 1] == L'(') { cut = z; break; }
            /* 空格后跟斜杠(如 " / Det") */
            if (text[z] == L' ' && text[z + 1] == L'/') { cut = z; break; }
            /* 空格后跟点(如 "1941 Campaign .scn") */
            if (text[z] == L' ' && text[z + 1] == L'.') { cut = z; break; }
        }
        if (cut >= 3) {
            int plen = cut;
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen != plen) continue;
                int ok = 1;
                for (int z = 0; z < plen; z++) {
                    if (lc_w(text[z]) != lc_w((wchar_t)(unsigned char)g_zh[i].key[z])) { ok = 0; break; }
                }
                if (!ok) continue;

                /* 尾巴原样保留：绝不再逐段拼装。
                 * 部队番号翻译已移除，任何"剥尾 + 拼回"都会产生
                 * 半中半英的碎片，比留英文更难核对。 */
                {
                    int tl = len - plen;
                    if (g_zh[i].wlen + tl + 2 > 500) break;
                    slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                    {
                        wchar_t *out = g_pool[slot];
                        int o;
                        memcpy(out, g_zh[i].wtext, g_zh[i].wlen * sizeof(wchar_t));
                        o = g_zh[i].wlen;
                        for (int z = plen; z < len; z++) out[o++] = text[z];
                        out[o] = 0;
                        {
                            LONG n = InterlockedIncrement(&g_dtHits);
                            if (n <= 40)
                                logfmt("[dt-suffix] \"%s\" + tail(%d) -> %d wchars\n",
                                       g_zh[i].key, tl, o);
                        }
                        return out;
                    }
                }
            }
        }
    }

    return text;
}

/* ================= 调用栈回溯 =================
 *
 * 为什么需要: 游戏只有一个文本绘制入口 (0x45CE60)，所有界面调它时
 * 的返回地址完全一样。第一版只记返回地址，结果 700 条捕获全挤在
 * 同一个调用点，区分不出界面。
 *
 * 第一版改成走 EBP 帧链，仍然失败 —— Release 构建把 EBP 当通用
 * 寄存器用，帧链是断的。实测捕获里一条链都没有。
 *
 * 现在改成扫描栈：入口 esp 往上找"看起来像返回地址"的 DWORD，
 * 判据是落在主模块 .text 段内。这样不依赖帧指针，FPO 也不影响。
 * 相邻重复的去掉，最多记 BT_DEPTH 层。 */
static DWORD g_textLo = 0, g_textHi = 0;

static void init_text_range(void)
{
    HMODULE h = GetModuleHandleW(NULL);
    BYTE *b;
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS32 *nt;
    IMAGE_SECTION_HEADER *sec;
    int i, n;
    if (!h) return;
    b = (BYTE *)h;
    dos = (IMAGE_DOS_HEADER *)b;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    nt = (IMAGE_NT_HEADERS32 *)(b + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;
    sec = IMAGE_FIRST_SECTION(nt);
    n = nt->FileHeader.NumberOfSections;
    for (i = 0; i < n; i++) {
        if (memcmp(sec[i].Name, ".text", 5) == 0) {
            g_textLo = (DWORD)(DWORD_PTR)b + sec[i].VirtualAddress;
            g_textHi = g_textLo + sec[i].Misc.VirtualSize;
            return;
        }
    }
}

static void walk_stack(void)
{
    int i, k = 0;
    DWORD *teb = (DWORD *)__readfsdword(0x18);
    DWORD stkHi = teb ? teb[1] : 0;
    DWORD p, end;

    for (i = 0; i < BT_DEPTH; i++) g_bt[i] = 0;
    if (!g_textLo || !g_textHi || !stkHi) return;

    g_bt[k++] = g_curRet;                 /* 第 0 层是直接返回地址 */

    p = g_curEsp;
    end = stkHi;
    if (end - p > 4096) end = p + 4096;   /* 只扫前 4KB，够覆盖调用链 */

    __try {
        while (p + 4 <= end && k < BT_DEPTH) {
            DWORD v = *(DWORD *)p;
            if (v >= g_textLo && v < g_textHi && v != g_bt[k - 1])
                g_bt[k++] = v;
            p += 4;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        /* 扫到栈边界了，保留已拿到的部分 */
    }
}

/* 把宽串写成可读的 UTF-8，非 ASCII 用 <XXXX> 转义，
 * 这样日志用记事本就能看，也方便脚本解析。 */
static void cap_write(const wchar_t *src, const wchar_t *dst)
{
    if (!g_capture || !g_capFile) return;
    int len = 0;
    while (len < 300 && src[len]) len++;
    if (len < 3) return;
    if (cap_seen(src, len)) return;
    if (InterlockedIncrement(&g_capCount) > CAP_N) return;

    char out[1200];
    int o = 0;
    const wchar_t *pair[2];
    /* 前置两列: 调用点返回地址、this 指针。
     * 有了它们才能反查这个串是哪个界面画的 —— 同一个文本
     * 在不同界面上的处理可能不一样。 */
    {
        int w, i;
        walk_stack();
        w = _snprintf(out + o, 80, "%08X@%08X",
                      (unsigned)g_curRet, (unsigned)g_curThis);
        if (w < 0) w = 0;
        o += w;
        for (i = 1; i < BT_DEPTH && g_bt[i]; i++) {
            w = _snprintf(out + o, 12, ":%08X", (unsigned)g_bt[i]);
            if (w < 0) break;
            o += w;
        }
    }
    out[o++] = '	';
    pair[0] = src; pair[1] = dst;
    for (int k = 0; k < 2; k++) {
        const wchar_t *p = pair[k] ? pair[k] : L"";
        if (k) out[o++] = '	';
        for (int i = 0; p[i] && i < 120 && o < 1100; i++) {
            wchar_t c = p[i];
            if (c >= 0x20 && c < 0x7f) out[o++] = (char)c;
            else o += _snprintf(out + o, 12, "<%04X>", (unsigned)c);
        }
    }
    out[o++] = '\n';
    fwrite(out, 1, o, g_capFile);
    /* 每次写都刷盘，崩溃也不丢数据 */
    fflush(g_capFile);
}

/* 前向声明：定义在文件后部的崩溃取证区。 */
static void recent_add(const wchar_t *t);

static wchar_t *dt_check_raw(wchar_t *text)
{
    recent_add(text);
    wchar_t *r = text;

    /* 匹配过程本身也可能出错（越界读、缓冲溢出）。捕获它，
     * 记下是哪条字符串，然后返回原文 —— 一条没翻总比游戏崩掉好。
     * 日志里出现 [dt-trap] 就说明这条串让匹配器踩线了。 */
    __try {
        r = dt_check_body(text);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        r = text;
        if (g_logBudget > 0 || g_logBudget == -1) {
            char u8[512];
            int n = 0;
            while (n < 120 && text[n]) n++;
            int m = WideCharToMultiByte(CP_UTF8, 0, text, n, u8,
                                        sizeof(u8) - 1, NULL, NULL);
            u8[m > 0 ? m : 0] = 0;
            logfmt("[dt-trap] %s\n", u8);
        }
    }

    cap_write(text, r);
    return r;
}

/* 对外入口: 替换文本，并把宽度补齐到原文。
 *
 * 只有真的发生了替换才补空格。.rdata 里已经被改写成中文的串，
 * dt_check_raw 找不到匹配会原样返回，此时不该补 —— 游戏是按
 * 中文宽度算的坐标，本来就是对的。 */
static wchar_t *dt_check(wchar_t *text)
{
    /* 不再补尾随空格: 实测绘制时尾随空格不产生宽度，
     * 补了也没用。宽度差改成直接调整 x 坐标，见 dt_check2。 */
    return dt_check_raw(text);
}

/* ================= 绘制坐标诊断 =================
 *
 * 游戏不用 GDI 画字。它的错误串里出现 "font atlas LockRect failure"、
 * "Render3DText Lock failure"，说明是自建字体图集 + D3D 渲染。
 * 所以钩 GDI32 的 ExtTextOutW 一次都不会命中（实测如此）。
 *
 * 真正的位置信息在 0x45CE60 的栈参数里。函数签名是 __thiscall
 * ret 0x14（5 个栈参数）:
 *   [orig+0x00] 返回地址
 *   [orig+0x04] arg1
 *   [orig+0x08] arg2
 *   [orig+0x0C] arg3
 *   [orig+0x10] arg4 = wchar_t* 文本
 *   [orig+0x14] arg5
 *
 * 把 5 个参数全记下来，再结合文本内容就能判断对齐方式。
 */
static wchar_t *dt_check2(wchar_t *text, DWORD *stk)
{
    wchar_t *r = dt_check(text);
    if (!text || !stk) return r;

    /* 区域采集: 读控件管理器，记下这次绘制挂在哪一个控件上。
     * 必须在 layout 开关判断之前 —— 两个诊断可以分开用。 */
    region_write(text, r, stk);

    /* ===== 对齐修正 =====
     *
     * 游戏先按英文宽度算好 x，再调这个函数。选项列表是右对齐的:
     * 实测所有标签的右边缘都落在 x=541 附近，S≈17.3 px/em:
     *
     *   战役结束条件   324 + 12.53em * 17.3 = 541
     *   德国士气修正   360 + 10.45em * 17.3 = 541
     *   恢复默认       411 +  7.56em * 17.3 = 541
     *   将领非战斗减员 301 + 13.83em * 17.3 = 541
     *
     * 换成中文后宽度变窄，从同一个 x 开始画，右边缘就短一截，
     * 视觉上整体偏左。补尾随空格无效（绘制时忽略空格），只能改坐标:
     *
     *   x' = x + (原宽 - 译宽) * S
     *
     * 这样右边缘回到原来的位置。居中排版的理论修正量是它的一半，
     * 但实测数据明确是右对齐，故取全量。
     */
    float x0 = *(float *)&stk[1];
    float dx = 0.0f;

    /* ===== 不再做坐标位移 =====
     *
     * 这里曾经按右边缘把文本往右推，用来修正"中文比英文短导致居中/
     * 右对齐偏移"。那个问题在补丁时机改到 DllMain 之后已经消失：
     * 游戏读到的就是中文，测量、排版、换行全按中文算，不需要修正。
     *
     * 留着反而有害。判据是"原文右边缘落在已知右对齐边缘 ±6px 内"，
     * 而 OOB 树形列表的行右边缘随文本长度在 234..643 之间分布，
     * 必然有些行撞上 541 被推走、邻居没被推 —— 表现为展开后整列
     * 参差不齐地往右跳。
     *
     * 左对齐列表本来就不需要位移。
     */

    if (!g_layoutLog || g_layoutCount > 4000) return r;

    int n = 0;
    while (n < 200 && text[n]) n++;
    if (n < 2) return r;

    g_layoutCount++;
    char u8[1024], u8r[1024];
    int m = WideCharToMultiByte(CP_UTF8, 0, text, n, u8, sizeof(u8) - 1,
                                NULL, NULL);
    if (m <= 0) return r;
    u8[m] = 0;

    int rn = 0;
    while (rn < 200 && r[rn]) rn++;
    int mr = WideCharToMultiByte(CP_UTF8, 0, r, rn, u8r,
                                 sizeof(u8r) - 1, NULL, NULL);
    if (mr < 0) mr = 0;
    u8r[mr] = 0;

    FILE *f = fopen("wite2_layout.txt", "ab");
    if (!f) return r;
    fprintf(f, "DRAW x0=%-7.1f dx=%-6.1f y=%-6.1f n=%-3d ow=%-5d rw=%-5d %s => %s\r\n",
            x0, dx, *(float *)&stk[2], n, text_w(text), text_w(r), u8, u8r);
    fclose(f);
    return r;
}

/* 钩子: 换掉栈上的文本指针后跳到 trampoline */
__declspec(naked) static void drawtext_hook(void)
{
    __asm {
        /* 先把入口时的 ebp / esp 存下来（必须在 push 之前取，
         * 否则拿到的是被我们改过的栈） */
        mov  [g_curEbp], ebp
        mov  [g_curEsp], esp
        pushfd
        push eax
        push ecx
        push edx
        /* 压了 16 字节, 现在 [esp+16] 就是入口时的 esp（指向返回地址）
         * 栈布局（相对当前 esp）:
         *   [+0]  保存的 edx
         *   [+4]  保存的 ecx  = __thiscall 的 this
         *   [+8]  保存的 eax
         *   [+C]  flags
         *   [+10] 调用者返回地址
         *   [+20] arg4 = 文本指针 */
        lea  eax, [esp + 16]
        mov  ecx, [eax]
        mov  [g_curRet], ecx
        mov  ecx, [esp + 4]
        mov  [g_curThis], ecx
        push eax                        /* arg2: 原始栈指针，用来读绘制坐标 */
        push dword ptr [eax + 0x10]     /* arg1: 文本指针 ([orig+0x10] = 第4个栈参数) */
        call dt_check2
        add  esp, 8
        mov  dword ptr [esp + 0x20], eax   /* 写回文本指针 */
        pop  edx
        pop  ecx
        pop  eax
        popfd
        jmp  dword ptr [g_dtTramp]
    }
}


/* ================= 布局诊断 =================
 *
 * 为什么需要: 「所有文本一起偏左」这类现象，光看字体文件判断不了
 * 到底是坐标被改了，还是文本本身变窄。必须拿到真实的绘制坐标。
 *
 * 钩 GDI32 的两个函数:
 *   GetTextExtentPoint32W  游戏量出多宽
 *   ExtTextOutW            游戏画在哪个 x/y
 *
 * 打开方式: 游戏目录放一个空的 wite2_layout.ini
 * 输出: wite2_layout.txt
 */
typedef BOOL (WINAPI *pExtTextOutW)(HDC, int, int, UINT, const RECT *,
                                    LPCWSTR, UINT, const INT *);
typedef BOOL (WINAPI *pGetTextExtent)(HDC, LPCWSTR, int, LPSIZE);

static pExtTextOutW   g_origExtTextOut = NULL;
static pGetTextExtent g_origGetExtent = NULL;

static void layout_dump(const char *kind, int a, int b,
                        const wchar_t *s, int n)
{
    if (!g_layoutLog || g_layoutCount > 4000) return;
    if (n < 2 || n > 200) return;
    g_layoutCount++;

    char u8[1024];
    int m = WideCharToMultiByte(CP_UTF8, 0, s, n, u8, sizeof(u8) - 1, NULL, NULL);
    if (m <= 0) return;
    u8[m] = 0;

    FILE *f = fopen("wite2_layout.txt", "ab");
    if (!f) return;
    fprintf(f, "%-8s %5d %5d  n=%3d  %s\r\n", kind, a, b, n, u8);
    fclose(f);
}

static BOOL WINAPI hook_ExtTextOutW(HDC hdc, int x, int y, UINT opt,
                                    const RECT *rc, LPCWSTR s, UINT n,
                                    const INT *dx)
{
    layout_dump("DRAW", x, y, s, (int)n);
    return g_origExtTextOut(hdc, x, y, opt, rc, s, n, dx);
}

static BOOL WINAPI hook_GetTextExtent(HDC hdc, LPCWSTR s, int n, LPSIZE sz)
{
    BOOL r = g_origGetExtent(hdc, s, n, sz);
    if (r) layout_dump("MEASURE", (int)sz->cx, (int)sz->cy, s, n);
    return r;
}

/* ================= MessageBox 钩子 =================

/*
 * 为什么要这个
 * ------------
 * 绘制钩子 0x85CE60 的第 4 个栈参数是 wchar_t*，只覆盖游戏自己画的文本。
 * 但一批玩家真能看到的报错走的是 Windows 消息框：
 *
 *     import:  MessageBoxA  YES      MessageBoxW  no
 *     .rdata: 'Can't play this game!'  'Unable to contact server!'
 *             'Can't upload to server [E1]' …
 *
 * MessageBoxA 收的是 **ANSI** 字符串，跟 wchar_t* 完全两条路，
 * 所以加再多词条也不会生效。这里直接钩导入表：
 *
 *   A 版：ANSI → UTF-8 → 查词 → UTF-8 → UTF-16，用 W 版弹
 *   W 版：直接过 dt_check_body（和绘制层同一套匹配）
 *
 * 为什么 A 版不试图改字符串内容：MessageBoxA 的转换由两层完成，
 * 我们只拿到 ANSI 指针，不知道底层代码页，所以统一转 UTF-8 再查表，
 * 命中就改用 MessageBoxW 直接传宽字符 —— 这样中文不依赖 CP_ACP。
 * 没命中就原样转回去调原来的 A 版，行为零变化。
 */
typedef int (WINAPI *pMessageBoxA)(HWND, LPCSTR, LPCSTR, UINT);
typedef int (WINAPI *pMessageBoxW)(HWND, LPCWSTR, LPCWSTR, UINT);
static pMessageBoxA g_origMsgBoxA = NULL;
static pMessageBoxW g_origMsgBoxW = NULL;

/* 前置声明：install_messagebox_hooks 定义在 install_iat_hook 之前，
 * 而后者在文件更下方（绘制钩子那一带）。 */
static void install_iat_hook(HMODULE exe, DWORD iatRva, void *repl,
                             void **orig);

/* ANSI → UTF-16。
 *
 * 必须用 CP_ACP：MessageBoxA 收到的 `text` 是 **ANSI**，不是 UTF-8。
 * 纯 ASCII 串两者恰好一样（我们的报错文案都是 ASCII），但带重音字符的
 * 文本（地名、将领名）用 CP_UTF8 会解码失败 —— 返回 0，调用者回退到
 * 原版 A 处理，不会出错。改对了才能后续支持非 ASCII 文案。
 */
static int ansi_to_w(const char *s, wchar_t *out, int cap)
{
    int n = MultiByteToWideChar(CP_ACP, 0, s, -1, out, cap);
    return n > 0 ? n - 1 : 0;
}

/* 把一个宽字符串过一遍词典（不改内存，返回是否命中 + 译文） */
static int translate_w(const wchar_t *src, wchar_t *dst, int cap)
{
    int len = 0;
    while (len < 1000 && src[len]) len++;
    if (len < 2 || len >= 1000) return 0;

    /* 先整串精确 */
    if (lookup_exact(src, len, dst, cap)) return 1;

    /* 再格式串模板 */
    for (int f = 0; f < g_nfmt; f++) {
        ZhEntry *e = &g_zh[g_fmt[f]];
        if (fmt_minlen(e->key) > len) continue;
        if (fmt_match(e->key, e->wtext, src, len, dst, cap - 1)) {
            return 1;
        }
    }
    return 0;
}

/* 惰性解析 MessageBoxW。
 *
 * 为什么必须要这一步
 * ------------------
 * 这个 exe 的 USER32 静态导入表里有 41 个函数，**包括 MessageBoxA，
 * 但没有 MessageBoxW**：
 *
 *   34  MessageBoxA  IAT VA=0x90c350   ← 有
 *       MessageBoxW                   ← 没有
 *
 * 所以 g_origMsgBoxW 永远是 NULL，A 钩子拿不到 W 版可调，
 * 中文根本发不出去（只能退化成原样输出英文）。
 *
 * user32 已经因为其他导入项被加载了，直接 GetProcAddress 拿来用，
 * 不增加任何新的 DLL 依赖。拿不到就返回 NULL，调用者回退。
 */
static pMessageBoxW resolve_msgbox_w(void)
{
    HMODULE u = GetModuleHandleA("user32.dll");
    if (!u) u = LoadLibraryA("user32.dll");
    if (!u) return NULL;
    return (pMessageBoxW)GetProcAddress(u, "MessageBoxW");
}

static int WINAPI hook_MessageBoxA(HWND h, LPCSTR text, LPCSTR cap,
                                   UINT type)
{
    /* 用栈缓冲而不是 static：两个钩子都可能被重入（消息框嵌套），
     * static 会让内层把外层的结果覆盖掉。1024 wchar = 4 KB，安全。 */
    wchar_t wtext[1024], wcap[1024], zh[1024];

    if (text) {
        pMessageBoxW w = g_origMsgBoxW;
        if (!w) w = g_origMsgBoxW = resolve_msgbox_w();
        if (w && ansi_to_w(text, wtext, 1024) > 0 &&
            translate_w(wtext, zh, 1024)) {
            int haveCap = cap && ansi_to_w(cap, wcap, 1024) > 0;
            return w(h, zh, haveCap ? wcap : NULL, type);
        }
    }
    if (g_origMsgBoxA) return g_origMsgBoxA(h, text, cap, type);
    return 0;
}

static int WINAPI hook_MessageBoxW(HWND h, LPCWSTR text, LPCWSTR cap,
                                   UINT type)
{
    wchar_t zh[1024];
    if (!g_origMsgBoxW) return 0;
    if (text && translate_w(text, zh, 1024))
        return g_origMsgBoxW(h, zh, cap, type);
    return g_origMsgBoxW(h, text, cap, type);
}

/* 钩 user32!MessageBoxA/W。走导入表，不碰函数体，比改 exe 代码安全。
 * 两种情况可能失败（都只记日志，不影响游戏）：
 *   - exe 没静态导入这两个函数（那时得另找方式）
 *   - 延迟加载（GetProcAddress 运行时解析），导入表里当时是空的 */
static void install_messagebox_hooks(HMODULE exe)
{
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)exe;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((BYTE *)exe + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;

    DWORD impRva = nt->OptionalHeader.DataDirectory
                       [IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    if (!impRva) { logfmt("[msgbox] 无导入表\n"); return; }

    IMAGE_IMPORT_DESCRIPTOR *imp =
        (IMAGE_IMPORT_DESCRIPTOR *)((BYTE *)exe + impRva);
    int hookedA = 0, hookedW = 0;

    for (; imp->Name; imp++) {
        const char *dll = (const char *)exe + imp->Name;
        /* user32 的常见写法 */
        if (_stricmp(dll, "user32.dll") != 0) continue;
        if (!imp->OriginalFirstThunk || !imp->FirstThunk) continue;

        IMAGE_THUNK_DATA *oft =
            (IMAGE_THUNK_DATA *)((BYTE *)exe + imp->OriginalFirstThunk);
        DWORD *iat = (DWORD *)((BYTE *)exe + imp->FirstThunk);

        for (; oft->u1.AddressOfData; oft++, iat++) {
            if (oft->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            IMAGE_IMPORT_BY_NAME *ibn =
                (IMAGE_IMPORT_BY_NAME *)((BYTE *)exe + oft->u1.AddressOfData);
            if (strcmp((const char *)ibn->Name, "MessageBoxA") == 0) {
                install_iat_hook(exe, (DWORD)((BYTE *)iat - (BYTE *)exe),
                                 (void *)hook_MessageBoxA,
                                 (void **)&g_origMsgBoxA);
                hookedA = 1;
            } else if (strcmp((const char *)ibn->Name, "MessageBoxW") == 0) {
                install_iat_hook(exe, (DWORD)((BYTE *)iat - (BYTE *)exe),
                                 (void *)hook_MessageBoxW,
                                 (void **)&g_origMsgBoxW);
                hookedW = 1;
            }
        }
        break;
    }
    logfmt("[msgbox] MessageBoxA=%s MessageBoxW=%s%s\n",
           hookedA ? "hooked" : "not-imported",
           hookedW ? "hooked" : "not-imported",
           (!hookedW && hookedA) ? " (W 走 GetProcAddress 惰性解析)" : "");
}

static void install_iat_hook(HMODULE exe, DWORD iatRva, void *repl, void **orig)
{
    BYTE *p = (BYTE *)exe + iatRva;
    DWORD oldProt;
    if (!VirtualProtect(p, 4, PAGE_READWRITE, &oldProt)) return;
    if (*orig == NULL) *orig = (void *)(*(DWORD *)p);
    *(DWORD *)p = (DWORD)repl;
    VirtualProtect(p, 4, oldProt, &oldProt);
}

static int install_drawtext_hook(HMODULE exe, DWORD rva)
{
    BYTE *fn = (BYTE *)exe + rva;
    /* 83 EC 38 56 8B F1 = sub esp,0x38 / push esi / mov esi,ecx */
    const BYTE sig[6] = { 0x83, 0xEC, 0x38, 0x56, 0x8B, 0xF1 };
    if (memcmp(fn, sig, 6) != 0) {
        logfmt("[dt] sig mismatch: %02X %02X %02X %02X %02X %02X\n",
               fn[0], fn[1], fn[2], fn[3], fn[4], fn[5]);
        return 0;
    }

    BYTE *tm = (BYTE *)VirtualAlloc(NULL, 64, MEM_COMMIT | MEM_RESERVE,
                                    PAGE_EXECUTE_READWRITE);
    if (!tm) { logfmt("[dt] alloc failed\n"); return 0; }

    memcpy(tm, fn, 6);
    tm[6] = 0xE9;
    DWORD rel = (DWORD)((fn + 6) - (tm + 11));
    memcpy(tm + 7, &rel, 4);
    g_dtTramp = (void *)tm;

    DWORD oldProt;
    if (!VirtualProtect(fn, 16, PAGE_EXECUTE_READWRITE, &oldProt)) return 0;
    DWORD jrel = (DWORD)((BYTE *)drawtext_hook - (fn + 5));
    fn[0] = 0xE9;
    memcpy(fn + 1, &jrel, 4);
    VirtualProtect(fn, 16, oldProt, &oldProt);
    FlushInstructionCache(GetCurrentProcess(), fn, 5);

    logfmt("[dt] hooked at %p tramp=%p\n", fn, tm);
    return 1;
}

/* ================= .dat 数据层钩子 =================
 *
 * 纯逻辑（解密/解压/查表/替换/打包）在 src/dat_layer.c，那边能离线测试。
 * 这里只负责把它挂到游戏的 fopen 上。
 *
 * 为什么不直接改游戏目录的 .dat
 * ------------------------------
 * 直接改文件有三个问题：
 *
 *   1. Steam 更新会覆盖，补丁失效
 *   2. 「验证文件完整性」会把改过的 .dat 判为损坏并重新下载
 *   3. 一旦某次更新改了 .dat 内部结构，改过的文件可能让游戏直接崩
 *
 * 所以改成**运行时拦截**：原文件一个字节不碰，fopen 打开 GenData\*.dat
 * 时现场解密、替换、重新打包，用 tmpfile() 返回匿名临时文件。
 * 游戏拿到的是普通 FILE*，fread/fclose 全部照常，它看不出区别。
 *
 * 拦截点
 * ------
 * `fopen` 在 RVA 0x45F5F0，整个 exe 里只有 3 个调用点：
 *
 *   0x5BA573 / 0x5BA9B1 / 0x5BAB23
 *
 * 全在 0x5BA4xx 那个 .dat 加载器里。函数体只有 23 字节：
 *
 *   push ebp; mov ebp,esp; push [ebp+0xc]; push -1; push [ebp+8]
 *   call 0x85F280; add esp,0xc; pop ebp; ret 8
 *
 * 也就是说 fopen(path,mode) 实际就是 impl(path, -1, mode)。所以 5 字节
 * JMP 直接盖住序言即可，**不需要 trampoline** —— 未命中时直接调 0x85F280。
 */

typedef FILE *(__cdecl *pFopenImpl)(const char *path, int flag,
                                    const char *mode);
static pFopenImpl g_fopenImpl = NULL;

/* 把 hook_text.c 的日志桥接给 dat_layer.c */
static void dat_log_bridge(const char *msg) { logfmt("%s", msg); }

/* 调用约定必须和原函数一致：
 *
 *   0x45F5F0  ... ret 8       -> __stdcall（被调方清 8 字节）
 *   0x85F280  ... add esp,0xc -> __cdecl（调方清 12 字节）
 *
 * 钩子用 __cdecl 就少清 8 字节栈，**游戏启动就会崩**。
 */
static FILE *__stdcall hook_fopen(const char *path, const char *mode)
{
    FILE *r = dat_redirect(path, g_fopenImpl);
    if (r) return r;
    /* 未命中：完全按原逻辑走 impl(path, -1, mode) */
    return g_fopenImpl(path, -1, mode);
}

static int install_fopen_hook(HMODULE exe)
{
    BYTE *fn = (BYTE *)exe + 0x45F5F0;
    /* 55 8B EC FF 75 0C = push ebp / mov ebp,esp / push [ebp+0xc] */
    const BYTE sig[6] = { 0x55, 0x8B, 0xEC, 0xFF, 0x75, 0x0C };
    DWORD oldProt, jrel;

    g_fopenImpl = (pFopenImpl)(void *)((BYTE *)exe + 0x85F280);
    dat_set_logger(dat_log_bridge);
    /* 映射表用相对路径：游戏工作目录就是游戏根目录 */
    dat_load_map("wite2_dat.tsv");

    if (memcmp(fn, sig, 6) != 0) {
        logfmt("[dat] fopen sig mismatch: %02X %02X %02X %02X %02X %02X\n",
               fn[0], fn[1], fn[2], fn[3], fn[4], fn[5]);
        return 0;
    }
    if (!VirtualProtect(fn, 16, PAGE_EXECUTE_READWRITE, &oldProt)) return 0;
    jrel = (DWORD)((BYTE *)hook_fopen - (fn + 5));
    fn[0] = 0xE9;
    memcpy(fn + 1, &jrel, 4);
    VirtualProtect(fn, 16, oldProt, &oldProt);
    FlushInstructionCache(GetCurrentProcess(), fn, 5);

    logfmt("[dat] fopen hooked at %p (impl=%p)\n", fn, g_fopenImpl);
    return 1;
}

/* ================= 入口 ================= */
/* ===== 补丁时机 =====
 *
 * 必须尽早。之前是 Sleep(2000) 之后再改 .rdata，结果对界面文本几乎
 * 无效 —— 游戏在启动阶段就把字符串复制进了自己的结构，2 秒后改
 * .rdata 已经晚了。
 *
 * 证据: "Fog of War" 在 .rdata 0x5B5DB0 是干净的完整串（前后都是 NUL），
 * 改写后绘制层日志里 ow 仍是英文宽度 1362，说明游戏画的是自己的副本。
 *
 * 后果不只是"改了没用": 所有可见翻译都得靠绘制层临时换指针，而绘制
 * 坐标是游戏按英文宽度算好的，换成长度不同的中文就必然错位。
 *
 * 所以改成在 DllMain(DLL_PROCESS_ATTACH) 里立刻改。这时游戏代码一行
 * 都还没跑，它之后复制的就是中文，测量、排版、换行全按中文算，
 * 对齐问题从根上消失。
 *
 * .rdata 在磁盘上是明文（已核实），所以早期改写可行。
 */
/* ================= 崩因取证 =================
 *
 * 崩溃已经反复出现几次，而“猜哪个字符串被改坏了”效率极低 —— 每次
 * 猜错就要玩家重跑一遍游戏。这里直接拿系统的异常记录：
 *
 *   异常代码  0xC0000005 = 访问违例
 *   出误地址  ExceptionInformation[1]
 *   读还是写  ExceptionInformation[0]
 *   崩在哪里  ExceptionAddress
 *
 * 有了崩在哪条指令，反汇编一看就知道它在查什么，比二分快得多。
 * 日志写到 wite2_crash.txt，不依赖任何游戏内的状态。 */
/* 最近处理过的字符串。崩溃时 dump 出来，最后几条就是嫌疑对象。
 * 只记前 100 字符，环形覆盖。 */
#define RECENT_N 64
static wchar_t g_recent[RECENT_N][104];
static volatile LONG g_recentIdx = 0;

static void recent_add(const wchar_t *t)
{
    if (!t) return;
    LONG i = InterlockedIncrement(&g_recentIdx) - 1;
    wchar_t *dst = g_recent[i % RECENT_N];
    int k = 0;
    for (; t[k] && k < 100; k++) dst[k] = t[k];
    dst[k] = 0;
}

static LONG WINAPI crash_filter(EXCEPTION_POINTERS *ep)
{
    PEXCEPTION_RECORD er = ep->ExceptionRecord;
    CONTEXT *ctx = ep->ContextRecord;
    FILE *f = fopen("wite2_crash.txt", "wb");
    if (!f) return EXCEPTION_CONTINUE_SEARCH;

    fprintf(f, "code=%08X addr=%08X\n",
            (unsigned)er->ExceptionCode,
            (unsigned)(DWORD_PTR)er->ExceptionAddress);
    if (er->NumberParameters >= 2) {
        const char *what = "?";
        switch ((int)er->ExceptionInformation[0]) {
        case 0: what = "READ"; break;
        case 1: what = "WRITE"; break;
        case 8: what = "EXEC"; break;
        }
        fprintf(f, "access=%s target=%08X\n", what,
                (unsigned)(DWORD_PTR)er->ExceptionInformation[1]);
    }
    if (ctx) {
        fprintf(f, "eip=%08X eax=%08X ebx=%08X ecx=%08X edx=%08X\n",
                (unsigned)ctx->Eip, (unsigned)ctx->Eax,
                (unsigned)ctx->Ebx, (unsigned)ctx->Ecx,
                (unsigned)ctx->Edx);
        fprintf(f, "esi=%08X edi=%08X ebp=%08X esp=%08X\n",
                (unsigned)ctx->Esi, (unsigned)ctx->Edi,
                (unsigned)ctx->Ebp, (unsigned)ctx->Esp);
        fprintf(f, "curRet=%08X curThis=%08X\n",
                (unsigned)g_curRet, (unsigned)g_curThis);
    }

    if (ctx) {
        DWORD *sp = (DWORD *)(DWORD_PTR)ctx->Esp;
        fprintf(f, "stack:");
        for (int i = 0; i < 64; i++) {
            DWORD v = 0;
            __try { v = sp[i]; }
            __except (EXCEPTION_EXECUTE_HANDLER) { break; }
            if (g_textLo && v >= g_textLo && v <= g_textHi)
                fprintf(f, " %08X", (unsigned)v);
        }
        fprintf(f, "\n");
    }
    fprintf(f, "recent strings (last 64 drawn):\n");
    {
        LONG cur = g_recentIdx;
        int show = cur < RECENT_N ? (int)cur : RECENT_N;
        for (int i = show; i > 0; i--) {
            const wchar_t *q = g_recent[(cur - i) % RECENT_N];
            fprintf(f, "  ");
            for (int z = 0; q[z] && z < 100; z++) {
                if ((unsigned)q[z] < 0x7f) fputc((char)q[z], f);
                else fprintf(f, "\\u%04X", (unsigned)q[z]);
            }
            fprintf(f, "\n");
        }
    }
    fclose(f);
    return EXCEPTION_CONTINUE_SEARCH;
}


/* VEH：游戏会覆盖 SetUnhandledExceptionFilter，这个不会。 */
static LONG CALLBACK veh_handler(PEXCEPTION_POINTERS ep)
{
    crash_filter(ep);
    return EXCEPTION_CONTINUE_SEARCH;
}

static void install_crash_handler(void)
{
    static int done = 0;
    if (done) return;
    done = 1;
    AddVectoredExceptionHandler(0 /* last chance */, veh_handler);
    logfmt("[crash] VEH 已安装 -> wite2_crash.txt\n");
}
static void patch_early(void)
{
    dict_load();

    /* 二分开关与改写日志必须在改写之前生效。
     * 曾经把这段放在 init_thread 里，而真正的改写发生在 DllMain 的
     * patch_early —— 等 init_thread 跑到时早就改完了，开关关了也没用，
     * wite2_patch.txt 永远是空的。定位崩溃需要这份日志，不能再晚。 */
    {
        FILE *f;
        f = fopen("wite2_nopatch.ini", "rb");
        if (f) { fclose(f); g_patchRdata = 0; g_patchData = 0; }
        f = fopen("wite2_nordata.ini", "rb");
        if (f) { fclose(f); g_patchRdata = 0; }
        f = fopen("wite2_nodata.ini", "rb");
        if (f) { fclose(f); g_patchData = 0; }
        f = fopen("wite2_rrange.ini", "rb");
        if (f) {
            unsigned lo = 0, hi = 0;
            if (fscanf(f, "%x %x", &lo, &hi) == 2 && hi > lo) {
                g_rLo = (DWORD)lo;
                g_rHi = (DWORD)hi;
            }
            fclose(f);
        }
        f = fopen("wite2_nodraw.ini", "rb");
        if (f) { fclose(f); g_drawHook = 0; }
    }

    logfmt("=== WITE2 中文化 v4 (内存改写) ===\n词典: %d 条 (.data 专用 %d 条)\n"
           "[patch] rdata=%d data=%d draw=%d range=%X..%X\n",
           g_ndic, g_nDataOnly, g_patchRdata, g_patchData, g_drawHook,
           (unsigned)g_rLo, (unsigned)g_rHi);

    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) { logfmt("[fail] 无 exe 句柄\n"); return; }
    /* 区域采集要用运行时基址重定位 0x1272800，先存下来。 */
    g_exeBase = (DWORD)(DWORD_PTR)exe;

    install_crash_handler();


    g_patLog = fopen("wite2_patch.txt", "wb");
    patch_all(exe);
    if (g_patLog) { fclose(g_patLog); g_patLog = NULL; }

    logfmt("\n改写 %d 处, 译文过长跳过 %d 条, 未找到 %d 条\n",
           g_patched, g_tooLong, g_notFound);
}

static DWORD WINAPI init_thread(LPVOID param)
{
    /* .rdata 已在 DllMain 里改完。这里等游戏起来再装绘制层 hook，
     * 用来兜底 / .rdata 里没有的串（数据文件读出来的、运行时拼接的）。 */
    Sleep(2000);

    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) return 0;

    /* 安全网: 早期补丁万一没生效（比如游戏自己重新加载了段），
     * 这里再补一次。patch_section 只匹配原文，已是中文的不会再改，
     * 所以重复调用是幂等的。
     * 注意：开关已在 patch_early 里读过，这里只依赖那两个全局变量。 */
    {
        g_patLog = fopen("wite2_patch.txt", "ab");
        patch_all(exe);
        if (g_patLog) { fclose(g_patLog); g_patLog = NULL; }
        logfmt("[patch] 二次校验 rdata=%d data=%d 新增 %d 处\n",
               g_patchRdata, g_patchData, g_patched);
    }


    /* 阶段2: 绘制层替换文本指针 */
    dict_build();
    g_logBudget = 50;
    {
        FILE *cf = fopen("wite2_collect.ini", "rb");
        if (cf) {
            g_collect = 1;
            fclose(cf);
            logfmt("[collect] 收集模式开启 -> wite2_strings.txt\n");
            CreateThread(NULL, 0, collect_writer, NULL, 0, NULL);
        }
    }
    if (g_drawHook) {
        install_drawtext_hook(exe, 0x45CE60);
        /* 消息框走 Windows API，不经绘制层，单独钩导入表。
         * 关掉的方式：游戏目录放 wite2_nomsgbox.ini */
        if (GetFileAttributesA("wite2_nomsgbox.ini") == INVALID_FILE_ATTRIBUTES)
            install_messagebox_hooks(exe);
        else
            logfmt("[msgbox] 已禁用（wite2_nomsgbox.ini）\n");
        /* 数据层：.dat 里的整句文本（事件正文等）在绘制层是逐词绘制的，
         * 钩子永远拿不到整句，只能在这里拦 fopen 现场替换。
         * 关掉的方式：游戏目录放 wite2_nodat.ini */
        if (GetFileAttributesA("wite2_nodat.ini") == INVALID_FILE_ATTRIBUTES)
            install_fopen_hook(exe);
        else
            logfmt("[dat] 已禁用（wite2_nodat.ini）\n");
    } else {
        logfmt("[draw] 已禁用（wite2_nodraw.ini）\n");
    }

    /* 布局诊断: 需要游戏目录有 wite2_layout.ini 才启用 */
    {
        FILE *lf = fopen("wite2_layout.ini", "rb");
        if (lf) {
            fclose(lf);
            g_layoutLog = 1;
            remove("wite2_layout.txt");
            /* 不钩 GDI32: 游戏用自建字体图集 + D3D 渲染，不走
             * ExtTextOutW。实测钩了零命中。坐标从 0x45CE60
             * 的栈参数拿（见 dt_check2）。 */
            logfmt("[layout] 诊断开启 -> wite2_layout.txt\n");
        }
    }

    /* 全量捕获: 需要游戏目录有 wite2_capture.ini */
    {
        FILE *cf = fopen("wite2_capture.ini", "rb");
        if (cf) {
            fclose(cf);
            remove("wite2_capture.txt");
            g_capHash = (unsigned *)calloc(CAP_N, sizeof(unsigned));
            g_capFile = fopen("wite2_capture.txt", "wb");
            if (g_capHash && g_capFile) {
                g_capture = 1;
                init_text_range();
                logfmt("[capture] 全量捕获开启 -> wite2_capture.txt "
                       "text=%08X-%08X\n",
                       (unsigned)g_textLo, (unsigned)g_textHi);
            }
        }
    }

    /* 区域采集: 放空的 wite2_regions.ini 单独开；开了 capture 也自动开。
     * 两个文件用途不同 —— capture 是"绘制过哪些串"（去重），
     * regions 是"每个串挂在哪一个控件/槽位上"（不去重）。 */
    {
        FILE *rf = fopen("wite2_regions.ini", "rb");
        int want = 0;
        if (rf) { fclose(rf); want = 1; }
        if (!want && g_capture) want = 1;
        if (want) {
            remove("wite2_regions.tsv");
            g_regFile = fopen("wite2_regions.tsv", "wb");
            g_regSeen = (unsigned *)calloc(REG_HASH, sizeof(unsigned));
            if (g_regFile) {
                g_regions = 1;
                fprintf(g_regFile,
                        "# ret\tthis\tthis_vt\tui_idx\tui_cap\ttxt_obj\t"
                        "ctl\tctl_vt\tx\ty\tstyle\tflags\tfont\t"
                        "text\tdst\n");
                logfmt("[regions] 采集开启 -> wite2_regions.tsv "
                       "ui=%08X\n", (unsigned)(g_exeBase + UI_ROOT_RVA));
            } else {
                logfmt("[regions] 打不开 wite2_regions.tsv\n");
            }
        }
    }

    return 0;
}

#ifdef ZH_SELFTEST
/* ===== 自测入口 =====
 *
 * 用 -DZH_SELFTEST 编译出来的 exe 直接从 stdin 读 UTF-8 行，
 * 走**真实的 C 匹配逻辑**，输出 "原文<TAB>译文<TAB>策略"。
 *
 * 存在的理由：tools/test_match.py 是 C 匹配逻辑的回归表，而游戏里的
 * 真实行为只能以此 exe 为准 —— 同一份 hook_text.c 编译出来，没有
 * 第二份实现可以漂移。
 */
int main(int argc, char **argv)
{
    static char line[4096];
    static wchar_t wbuf[1024];
    static char out[8192];

    dict_load();
    dict_build();
    /* --capture: 打开捕获，用来验证管线能写文件（不用进游戏） */
    int wantCap = (argc > 1 && strcmp(argv[1], "--capture") == 0);
    if (wantCap) {
        g_capHash = (unsigned *)calloc(CAP_N, sizeof(unsigned));
        g_capFile = fopen("selftest_capture.txt", "wb");
        if (g_capHash && g_capFile) g_capture = 1;
        else { fprintf(stderr, "capture init FAILED\n"); return 2; }
    }
    /* --stacktest: 验证栈扫描能拿到返回地址链 */
    if (argc > 1 && strcmp(argv[1], "--stacktest") == 0) {
        int i;
        init_text_range();
        g_curEsp = (DWORD)(DWORD_PTR)&wantCap;
        g_curRet = (DWORD)(DWORD_PTR)&main;
        walk_stack();
        fprintf(stderr, "text=%08X-%08X esp=%08X\n",
                g_textLo, g_textHi, g_curEsp);
        for (i = 0; i < BT_DEPTH; i++)
            fprintf(stderr, "  bt[%d]=%08X\n", i, g_bt[i]);
        return 0;
    }
    /* 关掉日志，避免污染 stdout */
    g_logBudget = -1;

    while (fgets(line, sizeof(line), stdin)) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = 0;
        char *cr = strchr(line, '\r');
        if (cr) *cr = 0;
        if (!line[0]) continue;

        int wn = MultiByteToWideChar(CP_UTF8, 0, line, -1, wbuf, 1024);
        if (wn <= 0) continue;
        wn--;  /* 去掉结尾 NUL 计数 */

        /* 走 dt_check_raw，捕获包装才生效 */
        wchar_t *r = wantCap ? dt_check_raw(wbuf) : dt_check_body(wbuf);
        int same = (r == wbuf);

        char *o = out;
        int n = WideCharToMultiByte(CP_UTF8, 0, wbuf, wn, o, 4000, NULL, NULL);
        o += n;
        *o++ = '	';
        r = r ? r : wbuf;
        int rl = 0;
        while (r[rl]) rl++;
        n = WideCharToMultiByte(CP_UTF8, 0, r, rl, o, 4000, NULL, NULL);
        o += n;
        *o++ = '	';
        strcpy(o, same ? "UNMATCHED" : "MATCHED");
        printf("%s\n", out);
    }
    return 0;
}

#else

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
        /* 先改 .rdata（游戏还没跑），再起线程装绘制层 hook。 */
        patch_early();
        HANDLE h = CreateThread(NULL, 0, init_thread, NULL, 0, NULL);
        if (h) CloseHandle(h);
    }
    return TRUE;
}

#endif