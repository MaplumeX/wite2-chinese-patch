/*
 * WITE2 中文化 v4 —— 内存内改写 .rdata 字符串
 * ===========================================
 *
 * 为什么换方案:
 *   v3 只 hook 了 DrawText, 主菜单标签成功, 但工具提示(Set to human
 *   controlled Axis player. 等)走另一条绘制路径, 完全没翻译。
 *   游戏里至少有三个文本绘制函数 (DrawText / DrawTextScaled / Render3DText),
 *   逐个逆向定位代价高且容易漏。
 *
 * v4 思路:
 *   所有界面文本都以 UTF-16 明文存放在 .rdata 中, 且是只读常量。
 *   启动时直接在内存里把英文改写成中文 —— 之后任何代码路径读到的都是中文,
 *   不管它用哪个绘制函数。
 *
 *   额外好处: 调用方测量宽度时读的也是中文, 排版自动正确,
 *   不再需要 v3 里那套 x 坐标修正。
 *
 *   中文比英文短得多 (UTF-16 下 "Game Options" 26 字节 vs "游戏选项" 10 字节),
 *   因此可以原地覆盖, 不需要移动任何数据。
 *
 * 安全约束:
 *   1. 只在译文不超长时改写 (len(zh) <= len(en)), 绝不越界
 *   2. 只匹配完整字符串 (前一个字符是 NUL, 后一个字符是 NUL),
 *      避免把 "Show Game Options screen" 里的 "Game Options" 当成独立串改写
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

/* ================= 兜底: DrawText hook ================= */
/* 用于 .rdata 里找不到的串 (运行时拼接的文本) */
typedef struct { wchar_t *key; wchar_t *val; unsigned hash; } Entry;
static Entry *g_tab = NULL;
static int g_cap = 0, g_cnt = 0;

static unsigned fnv1w(const wchar_t *s, int n)
{
    unsigned h = 2166136261u;
    for (int i = 0; i < n; i++) { h ^= (unsigned)(unsigned short)s[i]; h *= 16777619u; }
    return h;
}
static void tab_put(wchar_t *k, wchar_t *v)
{
    int len = (int)wcslen(k);
    unsigned h = fnv1w(k, len);
    int mask = g_cap - 1, i = (int)(h & mask);
    while (g_tab[i].key) {
        if (g_tab[i].hash == h && wcscmp(g_tab[i].key, k) == 0) {
            free(g_tab[i].val); g_tab[i].val = v; free(k); return;
        }
        i = (i + 1) & mask;
    }
    g_tab[i].key = k; g_tab[i].val = v; g_tab[i].hash = h; g_cnt++;
}
static const wchar_t *tab_get(const wchar_t *s, int len)
{
    if (!g_tab || !g_cnt) return NULL;
    unsigned h = fnv1w(s, len);
    int mask = g_cap - 1, i = (int)(h & mask);
    while (g_tab[i].key) {
        if (g_tab[i].hash == h && (int)wcslen(g_tab[i].key) == len
            && memcmp(g_tab[i].key, s, len * sizeof(wchar_t)) == 0)
            return g_tab[i].val;
        i = (i + 1) & mask;
    }
    return NULL;
}
static void tab_build(void)
{
    g_cap = 65536;
    g_tab = (Entry *)calloc(g_cap, sizeof(Entry));
    if (!g_tab) return;
    for (int d = 0; d < g_ndic; d++) {
        int n = (int)wcslen(g_dic[d].key);
        wchar_t *k = (wchar_t *)malloc((n + 1) * sizeof(wchar_t));
        if (!k) continue;
        memcpy(k, g_dic[d].key, (n + 1) * sizeof(wchar_t));
        tab_put(k, g_dic[d].val);
    }
}

static int readable(const void *p, int bytes)
{
    if (!p || (ULONG_PTR)p < 0x10000) return 0;
    MEMORY_BASIC_INFORMATION mbi;
    if (VirtualQuery(p, &mbi, sizeof(mbi)) != sizeof(mbi)) return 0;
    if (mbi.State != MEM_COMMIT) return 0;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    return (ULONG_PTR)p + bytes <= (ULONG_PTR)mbi.BaseAddress + mbi.RegionSize;
}

static DWORD g_resume = 0;
static int   g_hits = 0, g_logged = 0;

static void __cdecl process_params(DWORD *params)
{
    const wchar_t *p = (const wchar_t *)params[3];
    if (!readable(p, 4)) return;
    int n = 0;
    while (n < 512 && readable(p + n, 2) && p[n]) n++;
    if (n < 2 || n >= 512) return;
    const wchar_t *zh = tab_get(p, n);
    if (zh) {
        params[3] = (DWORD)zh;
        g_hits++;
        if (g_logged < 10) {
            logfmt("[hook] 兜底替换 #%d\n", g_hits);
            g_logged++;
        }
    }
}

__declspec(naked) static void hook_entry(void)
{
    __asm {
        pushad
        lea  eax, [esp + 0x24]
        push eax
        call process_params
        add  esp, 4
        popad
        sub  esp, 0x38
        push esi
        mov  esi, ecx
        mov  eax, g_resume
        jmp  eax
    }
}

static void install_hook(HMODULE exe, DWORD rva)
{
    BYTE *fn = (BYTE *)exe + rva;
    const BYTE sig[6] = { 0x83, 0xEC, 0x38, 0x56, 0x8B, 0xF1 };
    if (memcmp(fn, sig, 6) != 0) {
        logfmt("[hook] 特征不匹配, 跳过兜底 hook\n");
        return;
    }
    g_resume = (DWORD)(ULONG_PTR)(fn + 6);
    DWORD old;
    if (!VirtualProtect(fn, 16, PAGE_EXECUTE_READWRITE, &old)) return;
    DWORD rel = (DWORD)((BYTE *)hook_entry - (fn + 5));
    fn[0] = 0xE9;
    memcpy(fn + 1, &rel, 4);
    fn[5] = 0x90;
    VirtualProtect(fn, 16, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, 6);
    logfmt("[hook] 兜底 hook 已安装\n");
}

/* ================= 入口 ================= */
static DWORD WINAPI init_thread(LPVOID param)
{
    dict_load();
    tab_build();
    logfmt("=== WITE2 中文化 v4 (内存改写) ===\n词典: %d 条\n", g_ndic);

    Sleep(2000);

    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) { logfmt("[fail] 无 exe 句柄\n"); return 0; }

    patch_all(exe);

    logfmt("\n改写 %d 处, 译文过长跳过 %d 条, 未找到 %d 条\n",
           g_patched, g_tooLong, g_notFound);

    install_hook(exe, 0x45CE60);
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
