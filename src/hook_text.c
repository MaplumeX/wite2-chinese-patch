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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

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
    while (fgets(line, sizeof(line), f) && g_ndic < MAXENT) {
        if (line[0] == '#') continue;
        char *t = strchr(line, '\t');
        if (!t) continue;
        *t = 0;
        char *en = line, *zh = t + 1;
        size_t zl = strlen(zh);
        while (zl && (zh[zl-1] == '\n' || zh[zl-1] == '\r')) zh[--zl] = 0;

        /* 可选第三列: 作用域标记
         *   d = 只改 .data（天气/气候/飞机数据列这类只在 .data 出现的词）
         *   r = 只改 .rdata
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
        }

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
            memcpy(p, v, vlen * sizeof(wchar_t));
            memset(p + vlen, 0, (klen - vlen + 1) * sizeof(wchar_t));
            cnt++;
            g_patched++;
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

#define MAXZH 8192
typedef struct { char *key; int klen; wchar_t *wtext; int wlen; int cs; } ZhEntry;
static ZhEntry g_zh[MAXZH];
static int g_nzh = 0;

static void  *g_dtTramp = NULL;
static volatile LONG g_dtCalls = 0;
static volatile LONG g_dtHits = 0;
static int g_logBudget = 0;
static int g_dtLog = 0;

/* 布局诊断开关（见 dt_check2） */
static int g_layoutLog = 0;

/* 每 em 多少像素。由实测反解:
 * 选项列表标签右边缘恒为 541，S = (x2-x1)/(w1-w2) 得到 17.3。
 * 不同字号的界面这个值会不同，若某处过修/欠修就调这里。 */
#define PX_PER_EM 17.3f
static int g_layoutCount = 0;

/* 替换用的缓冲池: 每次调用占一个槽, 调用返回后才复用 */
#define POOL 32
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
            if (kw[j] > 0x7e || klen >= 255) { ok = 0; break; }
            key[klen++] = (char)kw[j];
        }
        if (!ok || klen < 3) continue;
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

    logfmt("[zh] %d entries ready (%d format templates)\n", g_nzh, g_nfmt);
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

/* ---------- 缩写展开: "Army Grp. Sout" -> "armygroupsout" ---------- */
static const struct { const char *a; const char *f; } ABBR[] = {
    {"grp",  "group"},   {"div",  "division"}, {"bde",  "brigade"},
    {"rgt",  "regiment"},{"mot",  "motorized"},{"inf",  "infantry"},
    {"pz",   "panzer"},  {"cav",  "cavalry"},  {"art",  "artillery"},
    {"hun",  "hungarian"},{"rum", "rumanian"}, {"mtd",  "mounted"},
    {"bn",   "battalion"},{"coy", "company"},  {"sqd",  "squadron"},
    {"mach", "machine"}, {"mgun", "machinegun"},{"det", "detachment"},
    {"cdo",  "commando"},{"para", "parachute"},{"abn",  "airborne"},
};
#define N_ABBR ((int)(sizeof(ABBR)/sizeof(ABBR[0])))

static int g_lastExpand = 0;

static int norm_expand(const wchar_t *s, int len, char *out, int cap)
{
    int o = 0, i = 0;
    g_lastExpand = 0;
    while (i < len && o < cap - 12) {
        wchar_t c = s[i];
        if ((c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z') ||
            (c >= L'0' && c <= L'9')) {
            char w[64];
            int wl = 0;
            while (i < len && wl < 60) {
                wchar_t d = s[i];
                if ((d >= L'A' && d <= L'Z'))      { w[wl++] = (char)(d - L'A' + L'a'); i++; }
                else if ((d >= L'a' && d <= L'z') ||
                         (d >= L'0' && d <= L'9')) { w[wl++] = (char)d; i++; }
                else break;
            }
            w[wl] = 0;
            const char *rep = NULL;
            for (int q = 0; q < N_ABBR; q++)
                if (strcmp(w, ABBR[q].a) == 0) { rep = ABBR[q].f; break; }
            if (rep) {
                int rl = (int)strlen(rep);
                if (o + rl < cap - 1) { memcpy(out + o, rep, rl); o += rl; }
                g_lastExpand = 1;
            } else if (wl) {
                if (o + wl < cap - 1) { memcpy(out + o, w, wl); o += wl; }
            }
        } else i++;
    }
    out[o] = 0;
    return o;
}

/* ---------- 数字+兵种缩写: "113th ID" -> "第113步兵师" ---------- */
static const struct { const char *ab; const wchar_t *zh; } DIVAB[] = {
    {"ID",  L"\u6B65\u5175\u5E08"},   /* 步兵师 */
    {"MD",  L"\u6469\u6258\u5316\u5E08"}, /* 摩托化师 */
    {"PD",  L"\u88C5\u7532\u5E08"},   /* 装甲师 */
    {"CD",  L"\u9A91\u5175\u5E08"},   /* 骑兵师 */
    {"SD",  L"\u8B66\u5907\u5E08"},   /* 警备师 */
    {"RD",  L"\u6B65\u5175\u5E08"},   /* 苏联步兵师 */
    {"TD",  L"\u5766\u514B\u5E08"},   /* 坦克师 */
    {"MGD", L"\u673A\u68B0\u5316\u5E08"}, /* 机械化师 */
    {"ABD", L"\u7A7A\u964D\u5E08"},   /* 空降师 */
    {"GD",  L"\u8FD1\u536B\u5E08"},   /* 近卫师 */
    {"CD",  L"\u9A91\u5175\u5E08"},
};
#define N_DIVAB ((int)(sizeof(DIVAB)/sizeof(DIVAB[0])))

/* 返回写入 out 的宽字符数, 0 表示不匹配 */
static int try_numbered(const wchar_t *text, int len, wchar_t *out, int cap)
{
    int i = 0;
    long num = 0;
    int nd = 0;
    while (i < len && text[i] >= L'0' && text[i] <= L'9' && nd < 5) {
        num = num * 10 + (text[i] - L'0'); i++; nd++;
    }
    if (nd < 1 || nd > 4) return 0;

    if (i + 2 > len) return 0;
    wchar_t a = text[i], b = text[i + 1];
    int okord = (a==L's'&&b==L't') || (a==L'n'&&b==L'd') ||
                (a==L'r'&&b==L'd') || (a==L't'&&b==L'h');
    if (!okord) return 0;
    i += 2;

    if (i >= len || text[i] != L' ') return 0;
    i++;

    int st = i;
    while (i < len && text[i] >= L'A' && text[i] <= L'Z') i++;
    int sl = i - st;
    if (sl < 1 || sl > 4 || i != len) return 0;

    char ab[8];
    for (int z = 0; z < sl; z++) ab[z] = (char)text[st + z];
    ab[sl] = 0;

    const wchar_t *zh = NULL;
    for (int q = 0; q < N_DIVAB; q++)
        if (strcmp(ab, DIVAB[q].ab) == 0) { zh = DIVAB[q].zh; break; }
    if (!zh) return 0;

    int o = 0;
    if (o < cap - 1) out[o++] = L'\u7B2C';   /* 第 */
    wchar_t dig[8];
    int dn = 0;
    long t = num;
    if (t == 0) dig[dn++] = L'0';
    while (t > 0) { dig[dn++] = (wchar_t)(L'0' + (t % 10)); t /= 10; }
    for (int z = dn - 1; z >= 0; z--) if (o < cap - 1) out[o++] = dig[z];
    for (int z = 0; zh[z] && o < cap - 1; z++) out[o++] = zh[z];
    out[o] = 0;
    return o;
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
 *   1. 整串精确匹配 (大小写不敏感)          <- 最安全, 覆盖绝大多数
 *   2. 剥离尾部后缀后精确匹配                <- "LI Corps (1/5)" 这种
 *   3. 数字 + 兵种缩写模式                  <- "113th ID"
 *   4. 截断前缀 (严格条件)                  <- "Army Grp. Sout"
 * 不再做任意位置的子串替换。
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

static wchar_t *dt_check_raw(wchar_t *text)
{
    if (!text) return text;
    InterlockedIncrement(&g_dtCalls);

    int len = 0;
    while (len < 300 && text[len]) len++;
    if (len < 3) return text;

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
        /* 快速剪枝: 模板至少要比实际串短（占位符吃掉至少 1 个） */
        if (e->klen > len) continue;
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
     * "LI Corps (1/5)"      -> 前缀 "LI Corps"
     * "16th MD   ( 24.9 )"  -> 前缀 "16th MD"
     * "1st Panzer Group   CU 1, SU 11" -> 前缀 "1st Panzer Group"
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

                int need = g_zh[i].wlen + (len - plen) + 1;
                if (need > 250) break;
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                wchar_t *out = g_pool[slot];
                memcpy(out, g_zh[i].wtext, g_zh[i].wlen * sizeof(wchar_t));
                int o = g_zh[i].wlen;
                for (int z = plen; z < len; z++) out[o++] = text[z];
                out[o] = 0;
                LONG n = InterlockedIncrement(&g_dtHits);
                if (n <= 40)
                    logfmt("[dt-suffix] \"%s\" + tail -> %d wchars\n",
                           g_zh[i].key, o);
                return out;
            }
        }
    }

    /* ---------- 策略 3: 数字 + 兵种缩写 ---------- */
    {
        slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
        int n = try_numbered(text, len, g_pool[slot], 250);
        if (n > 0) {
            LONG c = InterlockedIncrement(&g_dtHits);
            if (c <= 40) {
                char samp[80]; int sl = 0;
                for (int z = 0; z < len && z < 40; z++) samp[sl++] = (char)text[z];
                samp[sl] = 0;
                logfmt("[dt-num] \"%s\" -> %d wchars\n", samp, n);
            }
            return g_pool[slot];
        }
    }

    /* ---------- 策略 3.5: 缩写展开 + 前缀匹配 ----------
     * 只在确实展开了缩写时启用。例如 "Army Grp. Sout" 里的 "Grp." 被
     * 展开成 "Group", 归一化为 "armygroupsout", 可匹配
     * "Army Group South"。没有缩写被展开的普通单词(如 "National")
     * 不参与, 否则会误配到 NATIONALITY 这类长词。 */
    {
        char nt[400];
        int nl = norm_expand(text, len, nt, sizeof(nt));
        int didExpand = g_lastExpand;
        if (didExpand && nl >= 6) {
            int best = -1, bestLen = 1 << 30;
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen < nl || g_zh[i].klen >= bestLen) continue;
                if (nl * 2 < g_zh[i].klen) continue;
                wchar_t wk[256];
                int wkl = 0;
                for (int z = 0; z < g_zh[i].klen && wkl < 250; z++)
                    wk[wkl++] = (wchar_t)(unsigned char)g_zh[i].key[z];
                char nk[400];
                int nkl = norm_expand(wk, wkl, nk, sizeof(nk));
                if (nkl < nl) continue;
                if (memcmp(nt, nk, nl) != 0) continue;
                best = i;
                bestLen = g_zh[i].klen;
            }
            if (best >= 0) {
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                memcpy(g_pool[slot], g_zh[best].wtext,
                       (g_zh[best].wlen + 1) * sizeof(wchar_t));
                LONG c = InterlockedIncrement(&g_dtHits);
                if (c <= 40)
                    logfmt("[dt-abbr] \"%s\" (norm=%s) -> \"%s\"\n",
                           g_zh[best].key, nt, g_zh[best].key);
                return g_pool[slot];
            }
        }
    }

    /* ---------- 策略 4: 截断前缀 (严格) ----------
     * 绘制文本必须是某个 key 的**开头**, 且覆盖 key 至少 70%。
     * "Army Grp. Sout" -> "Army Group South" 需要先做缩写展开, 见下。
     * 这里只处理纯截断: "1st Panzer Grou" -> "1st Panzer Group" */
    {
        for (int i = 0; i < g_nzh; i++) {
            int kl = g_zh[i].klen;
            /* 门槛 len>=12: 长串被截断是常见现象, 短串不是。
             * 这能挡住 "National"(8) 误配 "NATIONALITY"(11)。 */
            if (kl <= len || len < 12) continue;
            if (len * 10 < kl * 7) continue;          /* 至少覆盖 70% */
            int ok = 1;
            for (int z = 0; z < len; z++) {
                if (lc_w(text[z]) != lc_w((wchar_t)(unsigned char)g_zh[i].key[z])) { ok = 0; break; }
            }
            if (!ok) continue;

            slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
            memcpy(g_pool[slot], g_zh[i].wtext,
                   (g_zh[i].wlen + 1) * sizeof(wchar_t));
            LONG n = InterlockedIncrement(&g_dtHits);
            if (n <= 40)
                logfmt("[dt-trunc] \"%s\" (len=%d) -> \"%s\"\n",
                       g_zh[i].key, len, g_zh[i].key);
            return g_pool[slot];
        }
    }

    /* ---------- 诊断: 记录含军事关键词但未命中的绘制文本 ---------- */
    if (g_dtLog < 150 && len >= 4 && len <= 80) {
        static const char *kw[] = { "Army", "Panzer", "Corps", "Division",
                                    "Group", "Bde", "Rgt", "Korps", "Armee" };
        int hit = 0;
        for (int q = 0; q < 9 && !hit; q++) {
            int kl = (int)strlen(kw[q]);
            for (int z = 0; z + kl <= len; z++) {
                int m = 1;
                for (int y = 0; y < kl; y++) {
                    if (text[z + y] != (wchar_t)(unsigned char)kw[q][y]) { m = 0; break; }
                }
                if (m) { hit = 1; break; }
            }
        }
        if (hit) {
            char samp[240];
            int sl = 0;
            for (int z = 0; z < len && z < 76; z++) {
                wchar_t c = text[z];
                if (c >= 0x20 && c < 0x7f) samp[sl++] = (char)c;
                else sl += _snprintf(samp + sl, 10, "<%04X>", (unsigned)c);
            }
            samp[sl] = 0;
            logfmt("[dt-mil] len=%d \"%s\"\n", len, samp);
            g_dtLog++;
        }
    }
    return text;
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
    if (r && r != text) {
        int wo = text_w(text), wr = text_w(r);
        if (wo > wr && x0 > -2000.0f && x0 < 6000.0f) {
            dx = (float)(wo - wr) * (PX_PER_EM / 256.0f);
            *(float *)&stk[1] = x0 + dx;
        }
    }

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
        pushfd
        push eax
        push ecx
        push edx
        /* 压了 16 字节, 现在 [esp+16] 就是入口时的 esp（指向返回地址） */
        lea  eax, [esp + 16]
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

/* ================= 入口 ================= */
static DWORD WINAPI init_thread(LPVOID param)
{
    dict_load();
    logfmt("=== WITE2 中文化 v4 (内存改写) ===\n词典: %d 条 (.data 专用 %d 条)\n",
           g_ndic, g_nDataOnly);

    Sleep(2000);

    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) { logfmt("[fail] 无 exe 句柄\n"); return 0; }

    patch_all(exe);

    logfmt("\n改写 %d 处, 译文过长跳过 %d 条, 未找到 %d 条\n",
           g_patched, g_tooLong, g_notFound);

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
    install_drawtext_hook(exe, 0x45CE60);

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

    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
        HANDLE h = CreateThread(NULL, 0, init_thread, NULL, 0, NULL);
        if (h) CloseHandle(h);
    }
    return TRUE;
}
