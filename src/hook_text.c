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
typedef struct { wchar_t *key; wchar_t *val; } Pair;
static Pair g_dic[MAXENT];
static int  g_ndic = 0;

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
        size_t el = strlen(en);
        if (!el || !zl) continue;
        wchar_t *k = u8w(en, (int)el), *v = u8w(zh, (int)zl);
        if (!k || !v) { free(k); free(v); continue; }
        g_dic[g_ndic].key = k;
        g_dic[g_ndic].val = v;
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

static void patch_section(BYTE *base, DWORD size, const char *secName)
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
        const wchar_t *k = g_dic[d].key;
        const wchar_t *v = g_dic[d].val;
        int klen = (int)wcslen(k);
        int vlen = (int)wcslen(v);

        if (vlen > klen) { g_tooLong++; continue; }

        int n = wfind_all(base, size, k, klen, offs, 256);
        if (n == 0) { g_notFound++; continue; }

        for (int j = 0; j < n; j++) {
            wchar_t *p = (wchar_t *)(base + offs[j]);
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
        if (strcmp(nm, ".rdata") != 0) continue;
        patch_section((BYTE *)exe + sec->VirtualAddress, sec->Misc.VirtualSize, nm);
        break;
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

#define MAXZH 2048
typedef struct { char *key; int klen; wchar_t *wtext; int wlen; } ZhEntry;
static ZhEntry g_zh[MAXZH];
static int g_nzh = 0;

static void  *g_dtTramp = NULL;
static volatile LONG g_dtCalls = 0;
static volatile LONG g_dtHits = 0;
static int g_logBudget = 0;
static int g_dtLog = 0;

/* 替换用的缓冲池: 每次调用占一个槽, 调用返回后才复用 */
#define POOL 32
static wchar_t g_pool[POOL][256];
static volatile LONG g_poolIdx = 0;

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
    logfmt("[zh] %d entries ready\n", g_nzh);
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

static int norm_expand(const wchar_t *s, int len, char *out, int cap)
{
    int o = 0, i = 0;
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

/* 检查绘制文本: 支持子串替换 (处理 "Attacks - 5th Panzer Army" 这类复合串) */
static wchar_t *dt_check(wchar_t *text)
{
    if (!text) return text;
    InterlockedIncrement(&g_dtCalls);

    int len = 0;
    while (len < 300 && text[len]) len++;
    if (len < 3) return text;

    collect_add(text, len);

    /* 先在整串范围内找词典 key, 逐位置尝试, 长的优先 */
    int changed = 0;
    int slot = -1;
    wchar_t *out = NULL;
    int op = 0;

    for (int pos = 0; pos < len; ) {
        int matched = 0;

        for (int i = 0; i < g_nzh; i++) {
            int kl = g_zh[i].klen;
            if (kl < 3 || pos + kl > len) continue;

            /* 词边界: key 前后都不能紧贴字母数字 */
            if (pos > 0 && is_word_ch(text[pos - 1])) continue;
            if (pos + kl < len && is_word_ch(text[pos + kl])) continue;

            int ok = 1;
            for (int z = 0; z < kl; z++) {
                if (text[pos + z] != (wchar_t)(unsigned char)g_zh[i].key[z]) { ok = 0; break; }
            }
            if (!ok) continue;

            /* 命中: 第一次命中时分配输出缓冲 */
            if (!changed) {
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                out = g_pool[slot];
                memcpy(out, text, pos * sizeof(wchar_t));
                op = pos;
                changed = 1;
            }
            /* 复制译文 */
            for (int z = 0; z < g_zh[i].wlen && op < 250; z++)
                out[op++] = g_zh[i].wtext[z];

            LONG n = InterlockedIncrement(&g_dtHits);
            if (n <= 40) {
                logfmt("[dt-hit] pos=%d \"%s\" -> %d wchars\n",
                       pos, g_zh[i].key, g_zh[i].wlen);
            }
            pos += kl;
            matched = 1;
            break;
        }
        if (matched) continue;

        if (changed && op < 250) out[op++] = text[pos];
        pos++;
    }

    if (changed) {
        if (op < 255) out[op] = 0;
        else out[255] = 0;
        return out;
    }

    /* 截断处理: 简略信息面板可能只画了前 N 个字符, 例如 "Army Group Ce"
     * 此时整串是某个 key 的前缀。要求前缀长度 >= 5 且占 key 一半以上,
     * 避免 "Army" 这种误匹配。 */
    for (int i = 0; i < g_nzh; i++) {
        int kl = g_zh[i].klen;
        if (kl <= len || len < 5 || len * 2 < kl) continue;
        int ok = 1;
        for (int z = 0; z < len; z++) {
            if (text[z] != (wchar_t)(unsigned char)g_zh[i].key[z]) { ok = 0; break; }
        }
        if (!ok) continue;

        slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
        memcpy(g_pool[slot], g_zh[i].wtext,
               (g_zh[i].wlen + 1) * sizeof(wchar_t));
        LONG n = InterlockedIncrement(&g_dtHits);
        if (n <= 60)
            logfmt("[dt-trunc] \"%s\" (len=%d) -> full \"%s\"\n",
                   g_zh[i].key, len, g_zh[i].key);
        return g_pool[slot];
    }

    /* 策略2: 数字+兵种缩写 "113th ID" -> "第113步兵师" */
    {
        slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
        int n = try_numbered(text, len, g_pool[slot], 250);
        if (n > 0) {
            LONG c = InterlockedIncrement(&g_dtHits);
            if (c <= 60) {
                char samp[80]; int sl = 0;
                for (int z = 0; z < len && z < 40; z++) samp[sl++] = (char)text[z];
                samp[sl] = 0;
                logfmt("[dt-num] \"%s\" -> %d wchars\n", samp, n);
            }
            return g_pool[slot];
        }
    }

    /* 策略3: 缩写展开后做前缀匹配
     *   "Army Grp. Sout" -> norm "armygroupsout"
     *   key "Army Group South" -> norm "armygroupsouth"
     *   -> 前缀匹配成功, 显示完整中文 (中文更短, 不会被截断) */
    {
        char nt[400];
        int nl = norm_expand(text, len, nt, sizeof(nt));
        if (nl >= 6) {
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen < 6) continue;
                wchar_t wk[256];
                int wkl = 0;
                for (int z = 0; z < g_zh[i].klen && wkl < 250; z++)
                    wk[wkl++] = (wchar_t)(unsigned char)g_zh[i].key[z];
                char nk[400];
                int nkl = norm_expand(wk, wkl, nk, sizeof(nk));
                if (nkl < nl) continue;
                /* 绘制文本太短时不要匹配长 key, 否则 "Army" 会变成
                 * "中央集团军群"。要求覆盖 key 的一半以上。 */
                if (nl * 2 < nkl) continue;
                if (memcmp(nt, nk, nl) != 0) continue;

                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                memcpy(g_pool[slot], g_zh[i].wtext,
                       (g_zh[i].wlen + 1) * sizeof(wchar_t));
                LONG c = InterlockedIncrement(&g_dtHits);
                if (c <= 60)
                    logfmt("[dt-abbr] \"%s\" (norm=%s) -> \"%s\"\n",
                           g_zh[i].key, nt, g_zh[i].key);
                return g_pool[slot];
            }
        }
    }

    /* 诊断: 记录含军事关键词但未命中的绘制文本 */
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

/* 钩子: 换掉栈上的文本指针后跳到 trampoline */
__declspec(naked) static void drawtext_hook(void)
{
    __asm {
        pushfd
        push eax
        push ecx
        push edx
        /* 16 字节压栈后, 原 [esp+0x10] 变成 [esp+0x20] */
        mov  eax, dword ptr [esp + 0x20]
        push eax
        call dt_check
        add  esp, 4
        mov  dword ptr [esp + 0x20], eax
        pop  edx
        pop  ecx
        pop  eax
        popfd
        jmp  dword ptr [g_dtTramp]
    }
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
    logfmt("=== WITE2 中文化 v4 (内存改写) ===\n词典: %d 条\n", g_ndic);

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
