/*
 * WITE2 代码段 dump 诊断工具
 * ==========================
 * 磁盘上的 .text 段是加密的（熵 8.000），但运行时内存中是解密的。
 * 本 DLL 在游戏加载完成后把解密的代码段 dump 到文件，供离线分析。
 *
 * 同时扫描 .rdata 中 UI 字符串的引用点，定位文本绘制函数。
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* ---------------- version.dll 转发 ---------------- */
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
FWD(DWORD, VerInstallFileA,            (DWORD a, LPCSTR b, LPCSTR c, LPCSTR d, LPSTR e, PUINT f), (a,b,c,d,e,f))
FWD(DWORD, VerInstallFileW,            (DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPWSTR e, PUINT f), (a,b,c,d,e,f))
FWD(UINT,  VerLanguageNameA,           (DWORD a, LPSTR b, UINT c), (a,b,c))
FWD(UINT,  VerLanguageNameW,           (DWORD a, LPWSTR b, UINT c), (a,b,c))

/* ---------------- 工具 ---------------- */
static double entropy(const BYTE *p, DWORD n)
{
    unsigned long cnt[256];
    memset(cnt, 0, sizeof(cnt));
    for (DWORD i = 0; i < n; i++) cnt[p[i]]++;
    double H = 0.0;
    for (int i = 0; i < 256; i++) {
        if (cnt[i]) {
            double q = (double)cnt[i] / (double)n;
            H -= q * (log(q) / log(2.0));
        }
    }
    return H;
}

/* 扫描代码中指向 target 的立即数引用 */
static void scan_refs(const BYTE *code, DWORD size, DWORD target,
                      const char *label, FILE *rep)
{
    int found = 0;
    for (DWORD i = 0; i + 6 < size; i++) {
        DWORD v = 0;
        if (code[i] == 0x68) {                      /* push imm32 */
            memcpy(&v, code + i + 1, 4);
            if (v == target) { fprintf(rep, "  %-28s push  @ +0x%06X\n", label, i); found++; }
        } else if (code[i] >= 0xB8 && code[i] <= 0xBF) {   /* mov r32, imm32 */
            memcpy(&v, code + i + 1, 4);
            if (v == target) { fprintf(rep, "  %-28s mov   @ +0x%06X\n", label, i); found++; }
        } else if (code[i] == 0x8D && (code[i+1] & 0xC7) == 0x05) { /* lea r32,[disp32] */
            memcpy(&v, code + i + 2, 4);
            if (v == target) { fprintf(rep, "  %-28s lea   @ +0x%06X\n", label, i); found++; }
        }
    }
    if (!found) fprintf(rep, "  %-28s (无引用)\n", label);
}

/* ---------------- 诊断主流程 ---------------- */
static DWORD WINAPI diag_thread(LPVOID param)
{
    Sleep(9000);   /* 等游戏完成初始化与代码解密 */

    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) return 0;

    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)exe;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((BYTE *)exe + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;

    FILE *rep = fopen("wite2_diag.txt", "wb");
    if (!rep) return 0;

    fprintf(rep, "WITE2 运行时诊断\n");
    fprintf(rep, "imagebase = %08X\n", (unsigned)(ULONG_PTR)exe);
    fprintf(rep, "sections  = %d\n\n", nt->FileHeader.NumberOfSections);

    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    BYTE *textPtr = NULL;
    DWORD textSize = 0;

    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        char name[9] = {0};
        memcpy(name, sec->Name, 8);
        DWORD vsz = sec->Misc.VirtualSize;
        BYTE *ptr = (BYTE *)exe + sec->VirtualAddress;
        if (vsz == 0 || vsz > 64 * 1024 * 1024) {
            fprintf(rep, "[%-8s] va=%08X vsz=%u (跳过)\n",
                    name, (unsigned)sec->VirtualAddress, (unsigned)vsz);
            continue;
        }
        double H = entropy(ptr, vsz);
        fprintf(rep, "[%-8s] va=%08X vsz=%8u entropy=%.4f %s\n",
                name, (unsigned)sec->VirtualAddress, (unsigned)vsz, H,
                H > 7.5 ? "加密" : "明文");

        /* dump 代码段 */
        if (strcmp(name, ".text") == 0 || strcmp(name, ".bind") == 0) {
            char fn[64];
            sprintf(fn, "wite2_%s.bin", name + 1);
            FILE *f = fopen(fn, "wb");
            if (f) {
                fwrite(ptr, 1, vsz, f);
                fclose(f);
                fprintf(rep, "         -> 已 dump 到 %s (%u 字节)\n", fn, (unsigned)vsz);
            }
        }
        if (strcmp(name, ".text") == 0) { textPtr = ptr; textSize = vsz; }
    }

    fprintf(rep, "\n=== 字符串引用扫描 (.text) ===\n");
    if (textPtr) {
        struct { DWORD rva; const char *name; } targets[] = {
            { 0x5bb3f0, "Game Options" },
            { 0x5bb40c, "Set the game options." },
            { 0x5baf2c, "Exit the game ?" },
            { 0x5bb768, "Multiplayer" },
            { 0x5bb7a8, "Load Saved Game" },
            { 0x5bb8ac, "Editor" },
            { 0x5bba04, "Preferences" },
            { 0x584be4, "Commander's Report" },
            { 0x50ea08, "FONT glyphs log" },
            { 0x50ec10, "DrawText VB Lock" },
            { 0x50ec90, "at DrawText Lock failure" },
            { 0x50ea80, "DrawTextScaled VB Lock" },
            { 0x50ed98, "Render3DText VB Lock" },
            { 0x50e6c6, "_Creating font texture" },
        };
        for (int i = 0; i < (int)(sizeof(targets)/sizeof(targets[0])); i++) {
            scan_refs(textPtr, textSize,
                      (DWORD)(ULONG_PTR)exe + targets[i].rva,
                      targets[i].name, rep);
        }
    } else {
        fprintf(rep, "  未找到 .text 段\n");
    }

    fprintf(rep, "\n完成。\n");
    fclose(rep);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
        HANDLE h = CreateThread(NULL, 0, diag_thread, NULL, 0, NULL);
        if (h) CloseHandle(h);
    }
    return TRUE;
}
