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

/* sec == 5 是封印：这些串永远是英文，原样显示。
 *
 * 为什么不能只从词典里删掉：部队番号名不是一条词条能管住的。
 * 删了 "Army Group North"，策略 2 会剥尾后段再拼回"集团军群北"；
 * 删了 "Luftflotte 1"，得到"航空队1"。这些串必须有一个地方
 * 在匹配链最前面直接短路，否则总有一条拼装路径能碰到。
 *
 * 名单在 _block_names.txt，构建时写进 TSV 第三列 x。
 * 只收「具体编制名」，不收形容词和国别词（Cavalry / Mountain /
 * Reserve / Finnish 这些是筛选框里的词条，翻了才对）。 */
#define MAXBLK 4096

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
            else if (sp[1] == 'x') sec = 5;   /* 封印：永不翻译 */
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
            memcpy(p, v, vlen * sizeof(wchar_t));
            memset(p + vlen, 0, (klen - vlen + 1) * sizeof(wchar_t));
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
typedef struct { char *key; int klen; wchar_t *wtext; int wlen; int cs; int blk; } ZhEntry;
static ZhEntry g_zh[MAXZH];
static int g_nzh = 0;
static int g_blk[MAXBLK];
static int g_nblk = 0;

static void  *g_dtTramp = NULL;
static volatile LONG g_dtCalls = 0;
static volatile LONG g_dtHits = 0;
static int g_logBudget = 0;
static int g_dtLog = 0;

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
static int g_noSeg = 0;   /* selftest --nosseg: 禁用策略 9，量出靠猜的比例 */
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
        g_zh[g_nzh].blk = (g_dic[d].sec == 5);
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

    /* 封印索引。单独列出来，因为每个绘制串都要查一遍，
     * 扫 9800 条太贵；封印名单只有百来条。 */
    for (int i = 0; i < g_nzh && g_nblk < MAXBLK; i++)
        if (g_zh[i].blk) g_blk[g_nblk++] = i;

    logfmt("[zh] %d entries ready (%d format templates, %d sealed)\n",
           g_nzh, g_nfmt, g_nblk);
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

/* 把英文序数前缀转成中文: "113th " -> "第113", "3rd " -> "第3"。
 * 也接受纯数字 "5 " 和罗马数字 "XXIV "（军级番号，原样保留）。
 * 返回写出的字符数，0 表示不是可识别的前缀。 */

/* 两字符前缀。dict_build 要求 key 长度 >= 3（避免误匹配），
 * 所以 HQ/SP/AA 这类进不了主词典，番号前缀需要单独一张小表。 */
static const char *g_shortPre[] = {
    "HQ", "SP", "AA", "MG", "LW", "SS", "FF", "BR", "US", "CA", "AU",
    "PO", "NZ", "BE", "IN", "GR", "NE", "SA", "SU", "RR", "GS", "KM"
};
static const char *g_shortZh[] = {
    "司令部", "自行", "防空", "机枪",
    "空军", "党卫军", "自由法国",
    "英国", "美国", "加拿大", "澳大利亚",
    "波兰", "新西兰", "比利时", "印度",
    "希腊", "荷兰", "南非", "苏联",
    "铁路", "通用勤务", "海军"
};

/* 查两字符前缀，返回 UTF-16 译文指针，无则 NULL */
static const wchar_t *lookup_short(const wchar_t *t, int len)
{
    if (len != 2) return NULL;
    static wchar_t buf[24][8];
    static int init = 0;
    if (!init) {
        init = 1;
        for (int i = 0; i < (int)(sizeof(g_shortZh)/sizeof(g_shortZh[0])); i++) {
            int n = MultiByteToWideChar(CP_UTF8, 0, g_shortZh[i], -1,
                                        buf[i], 8);
            if (n <= 0) buf[i][0] = 0;
        }
    }
    for (int i = 0; i < (int)(sizeof(g_shortPre)/sizeof(g_shortPre[0])); i++) {
        if (lc_w(t[0]) != lc_w((wchar_t)g_shortPre[i][0])) continue;
        if (lc_w(t[1]) != lc_w((wchar_t)g_shortPre[i][1])) continue;
        return buf[i];
    }
    return NULL;
}

/* 罗马数字转数值。从右往左扫，当前值小于右边值时相减。
 * 游戏里有非标准写法（"XXXXVIII" 表示 48，标准应写 XLVIII），
 * 这个算法对两种写法都能得出正确结果。 */
static int roman_val(wchar_t c)
{
    switch (c) {
    case L'I': return 1;
    case L'V': return 5;
    case L'X': return 10;
    case L'L': return 50;
    case L'C': return 100;
    case L'D': return 500;
    case L'M': return 1000;
    }
    return 0;
}

static int roman_to_int(const wchar_t *t, int len)
{
    int total = 0, prev = 0;
    for (int i = len - 1; i >= 0; i--) {
        int v = roman_val(t[i]);
        if (v == 0) return -1;
        if (v < prev) total -= v;
        else { total += v; prev = v; }
    }
    return total;
}

/* 拆分编号判定。
 *
 * 游戏把部队拆成 1/2/3 三个分遣队，名字写成 "3/213th Security Division"。
 * 这里的 3 是分遣队序号，不是番号；213th 才是番号。
 * 如果两个都转成 "第N"，会变成 "第3/第213警备师"，读起来像两个并列番号。
 *
 * 判据: 斜杠左边、不带序数后缀的纯数字。带了 th/st/nd/rd 就是番号，
 * 例如 "1/16th" 里的 16th 仍要转成 "第16"。
 */
static int is_split_index(const wchar_t *t, int len, const wchar_t *next)
{
    int i, d;
    const wchar_t *p;
    if (len < 1 || len > 3) return 0;
    if (!next || *next != L'/') return 0;
    for (i = 0; i < len; i++)
        if (t[i] < L'0' || t[i] > L'9') return 0;

    /* 斜杠右边必须紧跟"数字+序数后缀"，才说明右边是番号、左边是分遣队
     * 序号。反例是 "1/West Wall"（意大利第1西墙师）：右边是地名，
     * 左边 1 是番号，要转成 "第1西墙" 而不是保留成 "1/西墙"。 */
    p = next + 1;
    d = 0;
    while (*p >= L'0' && *p <= L'9') { p++; d++; }
    if (d == 0) return 0;
    if (!((p[0] == L't' && p[1] == L'h') || (p[0] == L's' && p[1] == L't') ||
          (p[0] == L'n' && p[1] == L'd') || (p[0] == L'r' && p[1] == L'd')))
        return 0;
    return 1;
}

/* 从 from 起跳过 nw 个词，返回结束位置（不含）。
 * 词之间用空格或斜杠分隔，连续分隔符跳过。 */
/* 从 p 起一个词的结束位置。
 *
 * 空格和斜杠断词。另外句点后紧跟大写字母也断，句点留在前一个词里 ——
 * 游戏会把缩写点后的空格吃掉："Mot. Corps" -> "Mot.Corps"，
 * 多句点的还连成一片："Mot. Nbl.W. Werfer" -> "Mot.Nbl.W.Werfer"。
 * 这样断开后，"Nbl." 和 "W." 是两个词，但贪心匹配取原文切片
 * 就能拼回 "Nbl.W." 这个完整条目。 */
static int word_end(const wchar_t *t, int p, int cut)
{
    while (p < cut) {
        if (t[p] == L' ' || t[p] == L'/' || t[p] == L'(' || t[p] == L')') break;
        if (t[p] == L'.' && p + 1 < cut &&
            ((t[p + 1] >= L'A' && t[p + 1] <= L'Z') ||
             (t[p + 1] >= L'a' && t[p + 1] <= L'z')))
            return p + 1;
        p++;
    }
    return p;
}

static int skip_words(const wchar_t *t, int from, int cut, int nw)
{
    int p = from, cnt = 0;
    while (p < cut && cnt < nw) {
        while (p < cut && (t[p] == L' ' || t[p] == L'/')) p++;
        if (p >= cut) break;
        p = word_end(t, p, cut);
        cnt++;
    }
    return p;
}

/* ---- 分段兜底翻译 ----------------------------------------------------
 *
 * 游戏很多显示串是拼出来的："  Army Group Center   CU 1, SU 1,   MP 50 "
 * 或者 "  Germany(Lublin) - Poor Road "。这类串永远不可能整串命中词典，
 * 只能切成"能译的段"和"原样段"。
 *
 * 关键约束是**全或无**：只要有一个字母 token 译不出来，整串原样返回。
 * 否则就会出现用户明确讨厌的那种碎片 —— "Show 生产 screen"。
 * 宁可整句留英文，也不能半中半英。
 */

/* 在绘制层词典里按宽串查，命中返回译文指针，否则 0 */
static int conv_num_prefix(const wchar_t *t, int len, wchar_t *out, int cap);
static int seg_passthru(const wchar_t *w, int n);

static const wchar_t *zfind_w(const wchar_t *w, int n)
{
    int i;
    if (n <= 0) return 0;
    for (i = 0; i < g_nzh; i++) {
        int z, ok = 1;
        if (g_zh[i].klen != n) continue;
        for (z = 0; z < n; z++) {
            if (lc_w(w[z]) != lc_w((wchar_t)(unsigned char)g_zh[i].key[z])) {
                ok = 0;
                break;
            }
        }
        if (ok) return g_zh[i].wtext;
    }
    return 0;
}

/* 单段翻译：先查词典，再试番号转换，最后试短串表。返回写入长度，0 表示译不了 */
static int ztry_seg(const wchar_t *w, int n, wchar_t *out, int cap)
{
    const wchar_t *t;
    /* 纯数字直接放行。否则 "27.6" 会被切成 27 和 6 两个 token，
     * 各自转成 "第27" "第6"，变成 "第27.第6"。 */
    {
        int i, allNum = (n > 0);
        for (i = 0; i < n; i++)
            if (w[i] < L'0' || w[i] > L'9') { allNum = 0; break; }
        if (allNum) {
            if (n >= cap) return 0;
            memcpy(out, w, n * sizeof(wchar_t));
            return n;
        }
    }
    t = zfind_w(w, n);
    if (t) {
        int l = (int)wcslen(t);
        if (l >= cap) return 0;
        memcpy(out, t, l * sizeof(wchar_t));
        return l;
    }
    {
        wchar_t b[64];
        int nn = (n < 60) ? n : 60, r;
        memcpy(b, w, nn * sizeof(wchar_t));
        b[nn] = 0;
        r = conv_num_prefix(b, nn, out, cap);
        if (r > 0) return r;
    }
    /* 单字符 token（"%d x %s" 的 x、"(a)" 的 a）原样保留 */
    if (n == 1) {
        memcpy(out, w, sizeof(wchar_t));
        return 1;
    }
    /* 数字+单个小写字母是层级标记（"Tier 1a" 的 1a），原样保留 */
    {
        int i = 0;
        while (i < n && w[i] >= L'0' && w[i] <= L'9') i++;
        if (i > 0 && i == n - 1 &&
            ((w[i] >= L'a' && w[i] <= L'z') || (w[i] >= L'A' && w[i] <= L'Z'))) {
            if (n >= cap) return 0;
            memcpy(out, w, n * sizeof(wchar_t));
            return n;
        }
    }
    /* 尾部带标点的 token："Range:" "Losses,"。剥掉标点再查，
     * 查到就把标点补回去 —— tooltip 里 "Label: 值" 到处都是。 */
    if (n > 2) {
        int tb = n;
        while (tb > 1 && (w[tb - 1] == L':' || w[tb - 1] == L',' ||
                          w[tb - 1] == L';' || w[tb - 1] == L'.'))
            tb--;
        if (tb < n && tb > 0) {
            const wchar_t *t3 = zfind_w(w, tb);
            if (t3) {
                int l3 = (int)wcslen(t3), k;
                if (l3 + (n - tb) < cap) {
                    memcpy(out, t3, l3 * sizeof(wchar_t));
                    int w2 = 0;
                    for (k = tb; k < n; k++)
                        if (w[k] != L'.') out[l3 + w2++] = w[k];
                    return l3 + w2;
                }
            }
        }
    }
    /* 缩写原样放行 */
    if (seg_passthru(w, n)) {
        if (n >= cap) return 0;
        memcpy(out, w, n * sizeof(wchar_t));
        return n;
    }
    return 0;
}

/* 压掉连续的「第」。
 * "No." 译成「第」，后面的 "1" 又会被番号转换加成「第1」，
 * 拼出来就是 "第第1群皇家空军"。中文里不会有两个连续的「第」。 */
static void squeeze_di(wchar_t *s)
{
    int i = 0, o = 0;
    while (s[i]) {
        if (s[i] == 0x7B2C && s[i + 1] == 0x7B2C) { i++; continue; }
        s[o++] = s[i++];
    }
    s[o] = 0;
}

static int is_alpha_w(wchar_t c)
{
    return (c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z');
}

/* 缩写/罗马数字：保留原文不算"译不出"。
 * 这是全或无规则的唯一例外 —— NKVD、MP、CU 这类本来就是缩写，
 * 逼着翻译只会更糟。 */
static int seg_passthru(const wchar_t *w, int n)
{
    int i, up = 1, roman = 1;
    if (n <= 0 || n > 6) return 0;
    for (i = 0; i < n; i++) {
        wchar_t c = w[i];
        if (c >= L'a' && c <= L'z') up = 0;
        if (c != L'I' && c != L'V' && c != L'X' && c != L'L' &&
            c != L'C' && c != L'D' && c != L'M') roman = 0;
        if (!((c >= L'A' && c <= L'Z') || (c >= L'0' && c <= L'9')))
            return 0;
    }
    return up || roman;
}

/* seg_fill 用的分词器：括号、逗号、冒号、分号、斜杠都是边界；
 * 句点只有在后面不是字母时才算边界（这样 Mot.Cr / Nbl.W. 仍是整token）。 */
static int seg_end(const wchar_t *t, int p, int len)
{
    while (p < len) {
        wchar_t c = t[p];
        if (c == L' ' || c == L'/' || c == L'(' || c == L')' ||
            c == L',' || c == L':' || c == L';' || c == L'[' || c == L']' ||
            c == L'%')
            break;
        /* 只有小数点的 "27.6" 才把句点当分隔。缩写点（Mot. / Bde.）留在
         * token 里，好让词典里的 "Mot." 条目整条命中 —— 否则会拼出
         * "摩托化. 军"。 */
        if (c == L'.' && p + 1 < len &&
            t[p + 1] >= L'0' && t[p + 1] <= L'9')
            break;
        p++;
    }
    return p;
}

/* 分段翻译。整串都能处理才返回 out，否则返回 0（调用方保留原文） */
static wchar_t *seg_fill(const wchar_t *src, wchar_t *out, int cap)
{
    int len = (int)wcslen(src);
    int i = 0, o = 0, pendingSpace = 0;

    if (len < 2 || len > 200) return 0;

    while (i < len) {
        if (o >= cap - 80) return 0;
        if (src[i] == L' ') { pendingSpace++; i++; continue; }
        /* %s / %d / %f 这类占位符整体原样输出。它们是结构，不是待译内容；
         * 整串按分段翻完后，运行时游戏再把值填进去。 */
        if (src[i] == L'%' && i + 1 < len) {
            wchar_t nx = src[i + 1];
            if (nx == L's' || nx == L'd' || nx == L'f' || nx == L'x' ||
                nx == L'u' || nx == L'c' || nx == L'%') {
                int fl = 2;
                if (o + fl + 2 < cap) {
                    if (pendingSpace && o > 0 &&
                        out[o - 1] < 0x2E80 && nx < 0x2E80)
                        out[o++] = L' ';
                    pendingSpace = 0;
                    out[o++] = src[i];
                    out[o++] = src[i + 1];
                }
                i += fl;
                continue;
            }
        }
        /* 数字也当 token 开头：否则 "102nd" 会被拆成 "102" + "nd"，
         * 而 "nd" 正好是序数后缀表里的条目。纯数字本身由 ztry_seg
         * 的缩写放行原样输出。 */
        if (!is_alpha_w(src[i]) && !(src[i] >= L'0' && src[i] <= L'9')) {
            /* 连续两个以上的空格是列对齐，原样保留 */
            if (pendingSpace >= 2) {
                while (pendingSpace > 0 && o + 1 < cap) { out[o++] = L' '; pendingSpace--; }
            } else if (pendingSpace && o > 0) out[o++] = L' ';
            pendingSpace = 0;
            out[o++] = src[i++];
            continue;
        }
        {
            int span, got = 0;
            wchar_t tmp[128];
            int tlen = 0;
            for (span = 8; span >= 1 && !got; span--) {
                int e = i, k;
                for (k = 0; k < span; k++) {
                    while (e < len && src[e] == L' ') e++;
                    if (e >= len) break;
                    {
                        int ne = seg_end(src, e, len);
                        if (ne == e) break;
                        e = ne;
                    }
                }
                if (k < span) continue;
                {
                    int he = e;
                    while (he > i && src[he - 1] == L' ') he--;
                    if (he <= i) continue;
                    tlen = ztry_seg(src + i, he - i, tmp, 120);
                    if (tlen > 0) { got = 1; i = he; }
                }
            }
            if (!got) return 0;              /* 全或无：有一处译不出就放弃整串 */
            if (o + tlen + 2 >= cap) return 0;
            /* 连续两个以上的空格是列对齐，原样保留 */
            if (pendingSpace >= 2) {
                while (pendingSpace > 0 && o + tlen + 2 < cap) {
                    out[o++] = L' ';
                    pendingSpace--;
                }
            } else if (pendingSpace && o > 0) {
                /* 保留空格的条件：
                 *  - 左边是标点（" - Poor" 省掉会变成 " -劣质"）
                 *  - 或者两侧都是拉丁字母（"West Wall"）
                 * 其余情况（中文侧）省略，免得出现 "第41 型"。 */
                wchar_t pc = out[o - 1];
                int pcPunct = !(is_alpha_w(pc) ||
                                (pc >= L'0' && pc <= L'9') || pc >= 0x2E80);
                if (pcPunct || (pc < 0x2E80 && tmp[0] < 0x2E80))
                    out[o++] = L' ';
            }
            pendingSpace = 0;
            memcpy(out + o, tmp, tlen * sizeof(wchar_t));
            o += tlen;
        }
    }
    out[o] = 0;
    squeeze_di(out);
    return out;
}

static int conv_num_prefix(const wchar_t *t, int len, wchar_t *out, int cap)
{
    int i = 0, o = 0;
    /* "1-17th" -> "1-第17"：连字符左边是分遣/支队序号，右边才是番号。
     * 罗马尼亚那些 "2-17th Rum. Mot. Anti-tank Company" 全是这个形态。 */
    {
        int d = 0;
        while (d < len && t[d] >= L'0' && t[d] <= L'9') d++;
        if (d > 0 && d + 2 < len && t[d] == L'-') {
            /* 右边必须真的是番号：数字+序数后缀，或者罗马数字。
             * 光看"左边数字+连字符"太松，"0-50"、"21-16" 这些数值范围
             * 会被当成番号变成 "0-第50" —— 兵牌上的数值就是这么坏的。*/
            int r2 = d + 1, ok2 = 0;
            if (r2 < len && t[r2] >= L'0' && t[r2] <= L'9') {
                int e2 = r2;
                while (e2 < len && t[e2] >= L'0' && t[e2] <= L'9') e2++;
                if (e2 + 1 < len) {
                    wchar_t a = t[e2], b = t[e2 + 1];
                    if ((a == L't' && b == L'h') || (a == L's' && b == L't') ||
                        (a == L'n' && b == L'd') || (a == L'r' && b == L'd'))
                        ok2 = 1;
                }
                if (!ok2 && e2 + 1 == len &&
                    (t[e2] == L'e' || t[e2] == L'E')) ok2 = 1;   /* 法语 5e */
            } else if (r2 < len) {
                int e2 = r2, rom = 1;
                while (e2 < len) {
                    wchar_t c = t[e2];
                    if (c != L'I' && c != L'V' && c != L'X' && c != L'L' &&
                        c != L'C' && c != L'D' && c != L'M') { rom = 0; break; }
                    e2++;
                }
                if (rom && e2 > r2) ok2 = 1;
            }
            if (!ok2) goto no_hyphen_prefix;
            {
            wchar_t rest[64];
            int rl = len - d - 1, r;
            if (rl > 0 && rl < 60) {
                memcpy(rest, t + d + 1, rl * sizeof(wchar_t));
                rest[rl] = 0;
                r = conv_num_prefix(rest, rl, out + d + 1, cap - d - 1);
                if (r > 0) {
                    int k;
                    for (k = 0; k < d; k++) out[k] = t[k];
                    out[d] = L'-';
                    return d + 1 + r;
                }
            }
            }
        }
no_hyphen_prefix:;
    }
    /* 罗马数字: 原样保留，前面不加"第"（"XXIV Panzer Corps" -> "XXIV装甲军"） */
    /* 罗马数字只认 I V X L C D M 七个字母。
     * 曾经写成 c >= L'I' && c <= L'L'，那个区间还包含 J 和 K，
     * 而且真正的罗马数字判断靠后面的 c == L'V' 等分支兜底，
     * 读起来容易误以为区间覆盖了 X。这里改成逐个显式比对。 */
    int allRoman = (len > 0);
    for (i = 0; i < len; i++) {
        wchar_t c = t[i];
        if (c != L'I' && c != L'V' && c != L'X' && c != L'L' &&
            c != L'C' && c != L'D' && c != L'M') { allRoman = 0; break; }
    }
    /* 罗马数字番号转成阿拉伯并加"第"：
     * "XII Corps" -> 第12军，而不是 XII军。
     * 用户看到 "XII军" 会认为没翻完 —— 罗马数字对中文读者不是数字。 */
    if (allRoman && len >= 2) {
        int v = roman_to_int(t, len);
        if (v > 0 && v < 10000) {
            wchar_t num[8];
            int nn = 0, x = v;
            wchar_t rev[8];
            while (x > 0 && nn < 8) { rev[nn++] = (wchar_t)(L'0' + x % 10); x /= 10; }
            int k = 0;
            if (o + 1 < cap) out[o++] = L'第';
            for (int z = nn - 1; z >= 0; z--) {
                if (o + 1 >= cap) break;
                out[o++] = rev[z];
            }
            (void)num; (void)k;
            out[o] = 0;
            return o;
        }
    }

    /* 阿拉伯数字，可带 st/nd/rd/th 后缀 */
    i = 0;
    if (o + 1 < cap) out[o++] = L'第';
    while (i < len && t[i] >= L'0' && t[i] <= L'9') {
        if (o + 1 >= cap) break;
        out[o++] = t[i++];
    }
    if (i == 0) return 0;              /* 没有数字 */
    /* 跳过序数后缀 */
    /* 边界必须是 i + 1 < len：两个字符的后缀只需要保证 t[i+1] 存在。
     * 曾经写成 i + 2 < len，导致 "79th"（len=4, i=2）判断失败，
     * 序数后缀没被消费，函数返回 0，前缀退回逐词翻译保留英文，
     * 于是出现 "79th步兵师" 而不是 "第79步兵师"。
     * 只有 "1st"（i=1, len=3, 1+2<3 为假… 但 st 分支用的是 i+1）侥幸正常。 */
    if (i + 1 < len && t[i] == L's' && t[i+1] == L't') i += 2;
    else if (i + 1 < len && t[i] == L'n' && t[i+1] == L'd') i += 2;
    else if (i + 1 < len && t[i] == L'r' && t[i+1] == L'd') i += 2;
    else if (i + 1 < len && t[i] == L't' && t[i+1] == L'h') i += 2;
    /* 法语序数: 5e / 1er / 2e / 3eme。北非和意大利部队用这种写法。 */
    else if (i < len && (t[i] == L'e' || t[i] == L'E')) {
        i++;
        if (i < len && (t[i] == L'r' || t[i] == L'R')) i++;
        if (i < len && (t[i] == L'e' || t[i] == L'E')) i++;
    }
    else if (i + 1 < len && (t[i] == L'r' || t[i] == L'R') &&
             (t[i+1] == L'e' || t[i+1] == L'E')) i += 2;
    out[o] = 0;
    return (i == len) ? o : 0;         /* 必须正好用完前缀 */
}

static int try_numbered(const wchar_t *text, int len, wchar_t *out, int cap)
{
    int i = 0, o = 0;
    long num = 0;
    int nd = 0;

    /* 拆分编号前缀 "3/97th ID"。游戏把部队拆成 1/2/3 三份，这个数字
     * 是分遣队序号，原样保留。判据和策略 1.7 一致：斜杠右边必须紧跟
     * 数字+序数后缀，否则左边的数字是番号。
     * 这里先探测，确认后面确实是"数字+序数"才吞掉前缀。 */
    {
        int p = 0, pd = 0;
        while (p < len && text[p] >= L'0' && text[p] <= L'9' && pd < 3) { p++; pd++; }
        if (pd >= 1 && p < len && text[p] == L'/') {
            int q = p + 1, qd = 0;
            while (q < len && text[q] >= L'0' && text[q] <= L'9') { q++; qd++; }
            int okOrd = (q + 1 < len) &&
                ((text[q]==L't'&&text[q+1]==L'h') || (text[q]==L's'&&text[q+1]==L't') ||
                 (text[q]==L'n'&&text[q+1]==L'd') || (text[q]==L'r'&&text[q+1]==L'd'));
            if (qd >= 1 && okOrd) {
                for (int z = 0; z <= p && o < cap - 1; z++) out[o++] = text[z];
                i = p + 1;
            }
        }
    }

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

    /* 缩写可以是大小写混合："4th Mnt Div" 里的 Mnt。
     * 原来只接受 A-Z，Mnt 直接判失败。这里放宽到字母，查表时转大写。 */
    int st = i;
    while (i < len && ((text[i] >= L'A' && text[i] <= L'Z') ||
                       (text[i] >= L'a' && text[i] <= L'z'))) i++;
    int sl = i - st;
    /* OOB 树形列表的列填充会在名字后留空格，剥掉再匹配，稍后补回 */
    int tailSp = 0;
    while (i < len && text[i] == L' ') { i++; tailSp++; }
    if (sl < 1 || sl > 4 || i != len) return 0;

    char ab[8];
    for (int z = 0; z < sl; z++) {
        wchar_t c = text[st + z];
        if (c >= L'a' && c <= L'z') c = (wchar_t)(c - 32);
        ab[z] = (char)c;
    }
    ab[sl] = 0;

    const wchar_t *zh = NULL;
    for (int q = 0; q < N_DIVAB; q++)
        if (strcmp(ab, DIVAB[q].ab) == 0) { zh = DIVAB[q].zh; break; }
    if (!zh) return 0;

    if (o < cap - 1) out[o++] = L'\u7B2C';   /* 第 */
    wchar_t dig[8];
    int dn = 0;
    long t = num;
    if (t == 0) dig[dn++] = L'0';
    while (t > 0) { dig[dn++] = (wchar_t)(L'0' + (t % 10)); t /= 10; }
    for (int z = dn - 1; z >= 0; z--) if (o < cap - 1) out[o++] = dig[z];
    for (int z = 0; zh[z] && o < cap - 1; z++) out[o++] = zh[z];
    for (int z = 0; z < tailSp && o < cap - 1; z++) out[o++] = L' ';
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

/* 文本是否以部队番号开头，如 "262nd ..." "XXXVI ..." "II/43rd ..."。
 *
 * 用来把部队名整个排除在翻译流程之外。部队显示名由 Units.csv 的
 * Name 和 OBs.csv 的 Suffix 在运行时拼成，组合数量巨大且无法逐条
 * 校对；一旦放任策略 2 剥尾后段再拼回去，就会得到
 *   "262nd Infantry Division" -> "第262步兵师"
 *   "II/42nd Mot. sFH Heavy Howitzer Battalion" -> "第2/第42摩托化...营"
 * 这类看起来像翻译、实际上是把两个词条硬凑起来的串。宁可显示
 * 英文原文，也不给读者一个无法核对的译名。 */
static int is_unit_designation(const wchar_t *t, int n)
{
    int i = 0;
    while (i < n && t[i] == L' ') i++;
    if (i >= n) return 0;

    /* SS / Gd. / Guards 开头的番号 */
    {
        static const wchar_t *pre[] = { L"SS ", L"Gd. ", L"Guards ",
                                        L"Gep. ", L"Lehr ", L"zbV ", NULL };
        for (int p = 0; pre[p]; p++) {
            int k = 0;
            while (pre[p][k]) k++;
            if (n - i < k) continue;
            int ok = 1;
            for (int z = 0; z < k; z++)
                if (lc_w(t[i + z]) != lc_w(pre[p][z])) { ok = 0; break; }
            if (ok) return 1;
        }
    }

    /* 阿拉伯数字 + 序数后缀（262nd / 1st / 42nd），或数字紧跟斜杠
     * （1/213th）。纯数字后面接空格不算："1941 Campaign" 是年份。 */
    if (t[i] >= L'0' && t[i] <= L'9') {
        int j = i;
        while (j < n && t[j] >= L'0' && t[j] <= L'9') j++;
        int digits = j - i;
        if (j < n && t[j] == L'/') return 1;
        if (digits <= 4 && j + 1 < n) {
            wchar_t a = lc_w(t[j]), b = lc_w(t[j + 1]);
            if ((a == L's' && b == L't') || (a == L'n' && b == L'd') ||
                (a == L'r' && b == L'd') || (a == L't' && b == L'h'))
                return 1;
        }
        return 0;
    }

    /* 罗马数字后跟空格或斜杠（XXXVI / XII/43rd）。至少 2 个字符，
     * 否则界面上的 "I" "V" "X" 单字母会被当成番号。 */
    {
        int j = i;
        while (j < n && t[j] >= L'A' && t[j] <= L'Z')
            if (t[j]=='I'||t[j]=='V'||t[j]=='X'||t[j]=='L'||
                t[j]=='C'||t[j]=='D'||t[j]=='M') j++; else break;
        if (j - i >= 2 && j < n && (t[j] == L' ' || t[j] == L'/'))
            return 1;
    }

    return 0;
}

static wchar_t *dt_check_body(wchar_t *text)
{
    if (!text) return text;
    InterlockedIncrement(&g_dtCalls);

    int len = 0;
    while (len < 300 && text[len]) len++;
    if (len < 3) return text;

    collect_add(text, len);

    int slot;

    /* ---------- 策略 -1: 封印串 ----------
     *
     * 具体编制名（Army Group Center、Luftflotte 1、OKH…）永远原样。
     * 必须排在最前面：放到后面的话，策略 2 剥个尾再拼回就变成
     * "集团军群北"，策略 3.5 展开缩写又会把 "Army Grp. Cent"
     * 改写成全称 —— 两种都不对。
     * 名单在 _block_names.txt，每条都经过确认是编制名而非通用词条。 */
    for (int b = 0; b < g_nblk; b++) {
        ZhEntry *e = &g_zh[g_blk[b]];
        if (e->klen != len) continue;
        int same = 1;
        for (int z = 0; z < len; z++) {
            wchar_t a = text[z];
            wchar_t c = (wchar_t)(unsigned char)e->key[z];
            if (e->cs ? (a != c) : (lc_w(a) != lc_w(c))) { same = 0; break; }
        }
        if (same) return text;
    }

    /* ---------- 策略 0: 拆分单位前缀 ----------
     *
     * 游戏允许把单位拆成几份，显示成 "1/213th Infantry Division" 或
     * "II/43rd ..."。前缀是纯机械的：数字原样，罗马数字转阿拉伯。
     * 后面的部分仍然是整条查表 —— 所以这里唯一的"拼接"就是把前缀
     * 拼回去，不做任何语言判断。
     *
     * 放在最前面：本体整条命中比任何分段逻辑都准。 */
    if (len > 2) {
        int p = 0, preLen = 0;
        wchar_t preOut[8];
        int isRoman = 0;
        /* 跳过层级标记 "- " 和缩进 */
        while (p < len && (text[p] == L' ' || text[p] == L'-')) p++;
        {
            int q = p;
            while (q < len && text[q] >= L'0' && text[q] <= L'9') q++;
            if (q > p && q < len && text[q] == L'/') {
                int z;
                for (z = p; z < q && preLen < 6; z++) preOut[preLen++] = text[z];
            } else {
                q = p;
                while (q < len && text[q] >= L'I' && text[q] <= L'M' &&
                       (text[q] == L'I' || text[q] == L'V' || text[q] == L'X' ||
                        text[q] == L'L' || text[q] == L'C' || text[q] == L'D' ||
                        text[q] == L'M')) q++;
                if (q > p && q < len && text[q] == L'/') {
                    int n = 0, z;
                    wchar_t r[8];
                    for (z = p; z < q; z++) {
                        wchar_t c = text[z];
                        n = n * 10 + (c == L'I' ? 1 : c == L'V' ? 5 : c == L'X' ? 10 :
                                      c == L'L' ? 50 : c == L'C' ? 100 :
                                      c == L'D' ? 500 : 1000);
                    }
                    if (n > 0 && n < 1000) {
                        wchar_t t[8];
                        int tl = 0, m;
                        while (n > 0 && tl < 6) { t[tl++] = L'0' + (n % 10); n /= 10; }
                        preOut[preLen++] = 0x7B2C;    /* 第 */
                        for (m = tl - 1; m >= 0; m--) preOut[preLen++] = t[m];
                        isRoman = 1;
                    }
                }
            }
        }
        (void)isRoman;
        if (preLen > 0) {
            /* 前缀后面是本体的起点 */
            int body = p;
            while (body < len && text[body] >= L'0' && text[body] <= L'9') body++;
            if (body < len && text[body] >= L'I' && text[body] <= L'M') {
                while (body < len && (text[body] == L'I' || text[body] == L'V' ||
                                      text[body] == L'X' || text[body] == L'L' ||
                                      text[body] == L'C' || text[body] == L'D' ||
                                      text[body] == L'M')) body++;
            }
            if (body < len && text[body] == L'/') {
                body++;
                /* 斜杠右边必须真的是番号（数字开头），左边才是分遣队序号。
                 * 否则 "1/West Wall" 的 1 是番号，会被误当成拆分前缀变成
                 * "1/西墙"。判据和 is_split_index 一致。 */
                if (body >= len || text[body] < L'0' || text[body] > L'9')
                    body = len;
                {
                    int bl = len - body;
                    int i;
                    for (i = 0; i < g_nzh; i++) {
                        int z, ok = 1;
                        if (g_zh[i].klen != bl) continue;
                        for (z = 0; z < bl; z++) {
                            wchar_t a = text[body + z];
                            wchar_t b = (wchar_t)(unsigned char)g_zh[i].key[z];
                            if (g_zh[i].cs ? (a != b) : (lc_w(a) != lc_w(b))) {
                                ok = 0;
                                break;
                            }
                        }
                        if (!ok) continue;
                        {
                            static wchar_t s0[POOL][512];
                            static int i0 = 0;
                            wchar_t *slot = s0[i0++ & (POOL - 1)];
                            int lead = p;
                            /* 保留原来的前导空格和 "- " 标记 */
                            if (lead + preLen + 1 + g_zh[i].wlen >= 500) break;
                            memcpy(slot, text, lead * sizeof(wchar_t));
                            memcpy(slot + lead, preOut, preLen * sizeof(wchar_t));
                            slot[lead + preLen] = L'/';
                            memcpy(slot + lead + preLen + 1, g_zh[i].wtext,
                                   g_zh[i].wlen * sizeof(wchar_t));
                            slot[lead + preLen + 1 + g_zh[i].wlen] = 0;
                            return slot;
                        }
                    }
                }
            }
        }
    }

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
    /* ---------- 番号开头的串：整串查不到就显示原文 ----------
     *
     * 放在所有“剥尾后拼回去”的策略之前。走到这里说明策略 1/1.5 的
     * 整串精确匹配已经失败，也就是词典里没有这个部队名。后面的
     * 策略 2/3/3.5/4/9 都会把它拆成“番号 + 编制词”再拼起来。 */
    if (is_unit_designation(text, len)) return text;

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

    /* ---------- 策略 1.7: 尾部兵种短语匹配 ----------
     *
     * 部队显示名 = 番号 + 兵种编制:
     *   "3rd Panzer Division"        -> 第3装甲师
     *   "XXIV Panzer Corps"          -> XXIV装甲军
     *   "HQ Panzer Corps"            -> 司令部装甲军
     *   "41 Tier 1a Panzer Division" -> 41型1a装甲师
     *
     * 逐个录入 8766 个单位的完整名不现实，剧本新增单位又会漏。
     * 所以拆开处理: 遍历每个空格位置，把右边当作"兵种短语"查词典，
     * 左边当番号翻译。从最长的后缀开始试，第一个命中就用。
     *
     * 番号翻译: 纯数字/序数转"第N"，其余逐词查词典，查不到的保留原文。
     * 只在后缀确实命中词典时才生效，不会误伤普通句子。 */
    {
        /* OOB 树形列表的名字常带尾随空格（" XXIV Mot. Corps "），
         * 直接拿整个串切分会让最后一段变成 "Corps " 而匹配不上。
         * 先剥掉尾随空格，拼好之后再补回去。 */
        int tn = len;
        while (tn > 0 && text[tn - 1] == L' ') tn--;
        int tail = len - tn;

        for (int cut = 1; cut < tn - 1; cut++) {
            /* 分隔符可以是空格，也可以是斜杠（"1/West Wall" 这种
             * 意大利/德军分队的写法）。空格不能连续。 */
            /* off = 分隔符占的字符数。空格/斜杠是 1。
             * 句点后紧跟大写字母时是 0：游戏吃掉了缩写点后的空格
             * （"Mot. Corps" -> "Mot.Corps"），句点要留在左边。 */
            int off;
            if (text[cut] == L'/') {
                off = 1;
            } else if (text[cut] == L' ' && text[cut - 1] != L' ') {
                off = 1;
            } else if (text[cut - 1] == L'.' &&
                       ((text[cut] >= L'A' && text[cut] <= L'Z') ||
                        (text[cut] >= L'a' && text[cut] <= L'z'))) {
                off = 0;
            } else {
                continue;
            }
            int slen = tn - cut - off;
            if (slen < 2) continue;

            /* 空格分隔、纯数字前缀、后缀是**单个单词** -> 数量不是番号。
             * "12 turns" 会被翻成 "第12回合"。
             * 后缀是多词短语就是兵种，要翻："1 Mot. Nebelwerfer Battalion"。
             * 斜杠分隔不受此限："1/West Wall" 的 1 是意大利第1西墙师。 */
            if (text[cut] == L' ') {
                int onlyDigits = 1, sufHasSpace = 0;
                for (int z = 0; z < cut; z++)
                    if (text[z] != L' ' && (text[z] < L'0' || text[z] > L'9'))
                        { onlyDigits = 0; break; }
                for (int z = cut + 1; z < tn; z++)
                    if (text[z] == L' ') { sufHasSpace = 1; break; }
                if (onlyDigits && !sufHasSpace) continue;
            }

            int found = -1;
            for (int i = 0; i < g_nzh; i++) {
                if (g_zh[i].klen != slen) continue;
                int ok = 1;
                for (int z = 0; z < slen; z++) {
                    if (lc_w(text[cut + off + z]) !=
                        lc_w((wchar_t)(unsigned char)g_zh[i].key[z])) {
                        ok = 0; break;
                    }
                }
                if (ok) { found = i; break; }
            }

            /* 尾部被截断时做前缀回退。
             *
             * 游戏有固定宽度的控件，长名字会被砍掉末尾：
             *   "18th Panzer Divisio"   缺 n
             *   "1/213th Sec Div"       缺 ision
             * 砍掉之后整串查不到词典，就退回英文显示 —— 而英文显示
             * 又长又会被继续砍，形成死循环。
             *
             * 判据收紧到四条，避免把普通词配到长条目上：
             *   1. 尾串至少 6 字符（挡住 "Div"(3) 这种）
             *   2. 覆盖候选 key 至少 55%（"Sec Div"/"Sec Division" = 58%）
             *   3. 候选必须唯一（"Div" 能配 Divisions/DIVIDE/... 就放弃）
             *   4. 截断点必须落在词中间。key[slen] 是空格说明尾串是个
             *      完整单词，不是被砍掉一半 —— "Armored" 会配到
             *      "Armored Cars" 得出 "第17装甲车"，那是误配。
             */
            int viaPrefix = 0;
            if (found < 0 && slen >= 6) {
                int cand = -1, nCand = 0;
                for (int i = 0; i < g_nzh; i++) {
                    int kl = g_zh[i].klen;
                    if (kl <= slen) continue;
                    if (slen * 100 < kl * 55) continue;
                    /* 截断点必须是词中间 */
                    if (g_zh[i].key[slen] == ' ') continue;
                    int ok = 1;
                    for (int z = 0; z < slen; z++) {
                        if (lc_w(text[cut + off + z]) !=
                            lc_w((wchar_t)(unsigned char)g_zh[i].key[z])) {
                            ok = 0; break;
                        }
                    }
                    if (!ok) continue;
                    cand = i;
                    if (++nCand > 1) break;
                }
                if (nCand == 1) { found = cand; viaPrefix = 1; }
            }
            if (found < 0) continue;

            /* 番号部分: 纯数字优先，否则逐词翻译。
             * 前导空格是 OOB 树形列表的缩进层级，必须原样保留，
             * 否则 " XXIV Mot. Corps" 会被当成番号 "XXIV" 而丢掉缩进。 */
            wchar_t pre[128];
            int pn = 0;
            while (pn < cut && pn < 8 && text[pn] == L' ') pre[pn] = L' ', pn++;
            pre[pn] = 0;
            if (is_split_index(text + pn, cut - pn, text + cut)) {
                /* "2/6th Guards" 在斜杠处切分时，左边的 2 是分遣队序号，
                 * 保留原样并把斜杠一起写出去，否则会得到 "第2第6近卫"。 */
                int k2 = cut - pn;
                memcpy(pre + pn, text + pn, k2 * sizeof(wchar_t));
                pn += k2;
                if (pn + 2 < 128) { pre[pn++] = L'/'; pre[pn] = 0; }
            } else {
                wchar_t tmp[128];
                int tn = conv_num_prefix(text + pn, cut - pn, tmp, 128);
                if (tn > 0) {
                    memcpy(pre + pn, tmp, (tn + 1) * sizeof(wchar_t));
                    pn += tn;
                } else {
                    pn = 0;   /* 让下面的逐词分支重来 */
                }
            }
            /* 番号部分词数/未译数。只在**后缀是截断匹配**时用它：
             * "Panzer Divisio" 会被切成 "Panzer"+"Divisio"，而后缀
             * Divisio 是靠前缀回退配到 Panzer Division 的。番号部分
             * Panzer 一个词都没翻出来，说明这不是"番号 + 兵种"结构，
             * 而是整串被截断。放弃本次切分，交给策略 4，否则会得到
             * "Panzer师"。
             *
             * 后缀精确命中时不能套这个判据 —— "Andrews Field" 的
             * Andrews 本来就查不到（地名不翻），拦掉会把已经翻好的
             * "Andrews机场" 也一起丢掉。 */
            int nTok = 0, nMiss = 0;
            if (pn == 0) {
                /* 重来一次，这次保留缩进 */
                while (pn < cut && pn < 8 && text[pn] == L' ')
                    pre[pn] = L' ', pn++;
                int st = pn;
                /* 保留 "-" "—" 这类层级标记，它们在 OOB 里表示下属 */
                while (st < cut && (text[st] == L'-' || text[st] == L'+' ||
                                    text[st] == L'—')) {
                    if (pn + 2 >= 128) break;
                    pre[pn++] = text[st++];
                    if (st < cut && text[st] == L' ') pre[pn++] = text[st++];
                }
                pre[pn] = 0;
                while (st < cut) {
                    int en = word_end(text, st, cut);
                    int wl = en - st;
                    if (wl <= 0) {
                        /* 到达分隔符。空格跳过，其他字符（括号）要保留，
                         * 否则 "16th(a)" 会变成 "第16a"。 */
                        if ((text[st] == L'(' || text[st] == L')') &&
                            pn + 2 < 128) {
                            pre[pn++] = text[st];
                            pre[pn] = 0;
                        }
                        st = en + 1;
                        continue;
                    }

                    nTok++;
                    /* 分遣队序号（"3/213th"）原样保留 */
                    if (is_split_index(text + st, wl, text + en)) {
                        if (pn + wl + 2 >= 128) { pn = 0; break; }
                        if (pn > 0 && text[st - 1] == L' ' && pre[pn - 1] < 0x2E80)
                            pre[pn++] = L' ';
                        memcpy(pre + pn, text + st, wl * sizeof(wchar_t));
                        pn += wl;
                        pre[pn] = 0;
                        /* 斜杠是分遣队标记，必须原样写出去 */
                        if (en < cut && text[en] == L'/') {
                            pre[pn++] = L'/';
                            pre[pn] = 0;
                        }
                        st = en + 1;   /* 斜杠已经写过了，跳过它 */
                        continue;
                    }
                    /* ===== 贪心最长匹配 =====
                     * 之前是纯逐词查词典，多词术语会被拆散：
                     *   "West Wall ..."    -> West=西 + Wall 查不到
                     *   "Vallo Alpino ..." -> 整串有条目却用不上
                     *   "Light Afrika ..." -> 同上
                     * 现在从当前位置起试最长的词组合（最多 6 个词），
                     * 命中就用并整段跳过。 */
                    int bestHit = -1, bestEnd = en;
                    {
                        int span;
                        for (span = 10; span >= 1; span--) {
                            int e2 = skip_words(text, st, cut, span);
                            if (e2 <= st || e2 > cut) continue;
                            int plen = e2 - st;
                            /* 多词短语内不能含斜杠（斜杠是分遣队标记） */
                            if (span > 1) {
                                int sl = 0, z;
                                for (z = st; z < e2; z++)
                                    if (text[z] == L'/') { sl = 1; break; }
                                if (sl) continue;
                            }
                            int i2;
                            for (i2 = 0; i2 < g_nzh; i2++) {
                                if (g_zh[i2].klen != plen) continue;
                                int ok = 1, z;
                                for (z = 0; z < plen; z++) {
                                    if (lc_w(text[st + z]) !=
                                        lc_w((wchar_t)(unsigned char)g_zh[i2].key[z])) {
                                        ok = 0; break;
                                    }
                                }
                                if (ok) { bestHit = i2; bestEnd = e2; break; }
                            }
                            if (bestHit >= 0) break;
                        }
                    }

                    /* 空格：只在前后都是 ASCII 时保留。原文空格用于分隔
                     * 拉丁词，中文之间不需要（"第41型1a" 而不是 "第41 型 1a"），
                     * 但 "(158th Res)" 内部的空格要留。 */
                    /* 数字番号。贪心已经命中一个多词短语就别再做单 token
                     * 转换 —— "II 'Folgore'" 整串有条目，但 II 单独转成
                     * 第2 会把这个短语拆掉。 */
                    if (bestHit < 0) {
                        wchar_t ntmp[24];
                        int nn = conv_num_prefix(text + st, wl, ntmp, 24);
                        if (nn > 0) {
                            if (pn + nn + 2 >= 128) { pn = 0; break; }
                            memcpy(pre + pn, ntmp, nn * sizeof(wchar_t));
                            pn += nn;
                            pre[pn] = 0;
                            if (en < cut && text[en] == L'/') {
                                pre[pn++] = L'/';
                                pre[pn] = 0;
                            }
                            st = en;
                            continue;
                        }
                    }

                    /* 看的是**译文**首字符，不是英文 key。
                     * "XXXXVI Mot." 的 key 是 'M'（ASCII），但译文是
                     * "摩托化"，两侧都是中文，不该留空格 —— 留了会变成
                     * "第46 摩托化军"。 */
                    if (st > 0 && text[st - 1] == L' ' && pn > 0 && pn + 2 < 128) {
                        wchar_t nxtCh = 0;
                        if (bestHit >= 0) nxtCh = g_zh[bestHit].wtext[0];
                        else {
                            const wchar_t *sp3 = lookup_short(text + st, wl);
                            nxtCh = (sp3 && sp3[0]) ? sp3[0] : text[st];
                        }
                        {
                            wchar_t pc = pre[pn - 1];
                            int pcPunct = !(is_alpha_w(pc) ||
                                            (pc >= L'0' && pc <= L'9') ||
                                            pc >= 0x2E80);
                            if (pcPunct || (pc < 0x2E80 && nxtCh < 0x2E80)) {
                                pre[pn++] = L' ';
                                pre[pn] = 0;
                            }
                        }
                    }

                    if (bestHit >= 0) {
                        int i2 = bestHit;
                        if (pn + g_zh[i2].wlen + 2 >= 128) { pn = 0; break; }
                        memcpy(pre + pn, g_zh[i2].wtext,
                               g_zh[i2].wlen * sizeof(wchar_t));
                        pn += g_zh[i2].wlen;
                    } else {
                        const wchar_t *sp2 = lookup_short(text + st, wl);
                        if (sp2 && sp2[0]) {
                            int w2 = 0;
                            while (sp2[w2]) w2++;
                            if (pn + w2 + 2 >= 128) { pn = 0; break; }
                            memcpy(pre + pn, sp2, w2 * sizeof(wchar_t));
                            pn += w2;
                        } else {
                            if (pn + wl + 2 >= 128) { pn = 0; break; }
                            memcpy(pre + pn, text + st, wl * sizeof(wchar_t));
                            pn += wl;
                            /* 纯大写/数字组成的 token 算"缩写放行"，不算漏译 */
                            {
                                /* 单字符 token（"(a)" 里的 a）不算漏译 */
                                int zz, passable = (wl > 0);
                                if (wl == 1) goto tok_ok;
                                for (zz = 0; zz < wl; zz++) {
                                    wchar_t c = text[st + zz];
                                    /* 允许 . ' - 出现在缩写里：
                                     * "O.T." "L.A.H." "M.C.A." "O'" */
                                    if (!((c >= L'A' && c <= L'Z') ||
                                          (c >= L'0' && c <= L'9') ||
                                          c == L'.' || c == (wchar_t)39 ||
                                          c == L'-')) {
                                        passable = 0;
                                        break;
                                    }
                                }
                                if (!passable) nMiss++;
                            }
tok_ok:;
                        }
                    }
                    pre[pn] = 0;
                    st = bestEnd;
                    /* 跳过分隔符，但斜杠和括号要保留 */
                    if (st < cut && text[st] == L'/') {
                        if (pn + 2 < 128) { pre[pn++] = L'/'; pre[pn] = 0; }
                        st++;
                    } else if (st < cut && (text[st] == L'(' || text[st] == L')')) {
                        if (pn + 2 < 128) { pre[pn++] = text[st]; pre[pn] = 0; }
                        st++;
                    } else if (st < cut && text[st] == L' ') {
                        st++;
                    }
                }
            }
            if (pn <= 0) continue;
            /* 全或无：番号部分只要有一个词译不出，就放弃这次切分。
             * 否则会拼出 "Enter第2步兵师近卫状态for the corps" 这种
             * 半中半英的碎片 —— 那正是用户最反感的结果。 */
            if (nTok > 0 && nMiss > 0) continue;


            if (pn + g_zh[found].wlen + tail + 1 > 250) break;
            slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
            wchar_t *o2 = g_pool[slot];
            memcpy(o2, pre, pn * sizeof(wchar_t));
            memcpy(o2 + pn, g_zh[found].wtext,
                   g_zh[found].wlen * sizeof(wchar_t));
            for (int z = 0; z < tail; z++) o2[pn + g_zh[found].wlen + z] = L' ';
            o2[pn + g_zh[found].wlen + tail] = 0;
            squeeze_di(o2);
            LONG h = InterlockedIncrement(&g_dtHits);
            if (h <= 40)
                logfmt("[dt-unit] \"%s\" -> %d wchars\n",
                       g_zh[found].key, g_zh[found].wlen);
            return o2;
        }
    }

    /* ---------- 策略 8: 括号尾巴 ----------
     * "Franz Halder  (Generaloberst)" —— 将领姓名本来就不翻，但军衔要翻。
     * 把最后一个 " (" 之后的部分单独拿去翻，头部译不了就原样保留。
     * 注意每段都用独立缓冲：seg_fill 失败时会写出半截内容，
     * 直接在输出缓冲上调会把原文覆盖成 "卡尔rl Weisenberger"。 */
    if (len < 240) {
        int p = -1, z;
        for (z = len - 1; z > 1; z--)
            if (text[z] == L'(' && text[z - 1] == L' ') { p = z; break; }
        if (p > 0 && text[len - 1] == L')') {
            static wchar_t s8[POOL][512];
            static int i8 = 0;
            wchar_t *slot = s8[i8++ & (POOL - 1)];
            wchar_t tail[128], tOut[192], head[256], hOut[256];
            int tl = len - p, hl = p;
            memcpy(tail, text + p, tl * sizeof(wchar_t));
            tail[tl] = 0;
            while (hl > 0 && text[hl - 1] == L' ') hl--;
            memcpy(head, text, hl * sizeof(wchar_t));
            head[hl] = 0;
            if (seg_fill(tail, tOut, 192)) {
                if (hl > 0 && seg_fill(head, hOut, 256)) {
                    int a = (int)wcslen(hOut), b = (int)wcslen(tOut), k;
                    while (a > 0 && hOut[a - 1] == L' ') a--;
                    if (a + (p - hl) + b < 500) {
                        memcpy(slot, hOut, a * sizeof(wchar_t));
                        for (k = 0; k < p - hl; k++) slot[a + k] = L' ';
                        memcpy(slot + a + (p - hl), tOut, (b + 1) * sizeof(wchar_t));
                        return slot;
                    }
                } else if (p + tl < 500) {
                    int b = (int)wcslen(tOut);
                    memcpy(slot, text, p * sizeof(wchar_t));
                    memcpy(slot + p, tOut, (b + 1) * sizeof(wchar_t));
                    return slot;
                }
            }
        }
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

                /* 余下部分也要处理。否则 "PRODUCED     CONSUMED  AVAIL ..."
                 * 只翻第一个词，后面整排列头留英文 —— 这正是碎片。
                 * 余下部分整体走 seg_fill，成功就拼接，失败就保留原文。 */
                {
                    wchar_t tailIn[512], tailOut[512];
                    int tl = len - plen, to = -1, need, o, z;
                    if (tl < 500) {
                        memcpy(tailIn, text + plen, tl * sizeof(wchar_t));
                        tailIn[tl] = 0;
                        if (seg_fill(tailIn, tailOut, 512))
                            to = (int)wcslen(tailOut);
                    }
                    need = g_zh[i].wlen + ((to >= 0) ? to : tl) + 2;
                    if (need > 500) break;
                    slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                    {
                        wchar_t *out = g_pool[slot];
                        memcpy(out, g_zh[i].wtext, g_zh[i].wlen * sizeof(wchar_t));
                        o = g_zh[i].wlen;
                        if (to >= 0) {
                            memcpy(out + o, tailOut, (to + 1) * sizeof(wchar_t));
                            o += to;
                        } else {
                            for (z = plen; z < len; z++) out[o++] = text[z];
                            out[o] = 0;
                        }
                        {
                            LONG n = InterlockedIncrement(&g_dtHits);
                            if (n <= 40)
                                logfmt("[dt-suffix] \"%s\" + tail(%d) -> %d wchars\n",
                                       g_zh[i].key, to, o);
                        }
                        return out;
                    }
                }
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

    /* ---------- 策略 3.1: 斜杠分隔的双番号 ----------
     * "CCXVIII/43rd" "LXXXVIII/43rd"：两边都是番号（罗马或阿拉伯），
     * 斜杠表示隶属或合并。两边都转成"第N"。
     *
     * 判据必须有至少一边带序数后缀或罗马数字，否则 "218/350"
     * （飞机可用/总数）会被翻成 "第218/第350"。纯数字比值在界面上
     * 到处都是：数量、百分比、坐标、回合。 */
    {
        int sl = -1;
        for (int z = 1; z < len - 1; z++)
            if (text[z] == L'/') { sl = z; break; }
        if (sl > 0) {
            wchar_t a[32], b[32];
            int an = conv_num_prefix(text, sl, a, 32);
            int bn = conv_num_prefix(text + sl + 1, len - sl - 1, b, 32);
            int isDual = 0;
            if (an > 0 && bn > 0) {
                /* 左边是罗马数字（>=2 位）算番号 */
                int romA = (sl >= 2);
                for (int z = 0; z < sl && romA; z++) {
                    wchar_t c = text[z];
                    if (c != L'I' && c != L'V' && c != L'X' && c != L'L' &&
                        c != L'C' && c != L'D' && c != L'M') romA = 0;
                }
                /* 任一边带序数后缀 */
                int ordA = 0, ordB = 0;
                if (sl >= 3) {
                    wchar_t a2 = text[sl - 2], b2 = text[sl - 1];
                    ordA = (a2==L't'&&b2==L'h') || (a2==L's'&&b2==L't') ||
                           (a2==L'n'&&b2==L'd') || (a2==L'r'&&b2==L'd');
                }
                if (len - sl - 1 >= 3) {
                    wchar_t a2 = text[len - 2], b2 = text[len - 1];
                    ordB = (a2==L't'&&b2==L'h') || (a2==L's'&&b2==L't') ||
                           (a2==L'n'&&b2==L'd') || (a2==L'r'&&b2==L'd');
                }
                isDual = (romA || ordA || ordB);
            }
            if (an > 0 && bn > 0 && isDual) {
                slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
                int o2 = 0;
                for (int z = 0; z < an; z++) g_pool[slot][o2++] = a[z];
                g_pool[slot][o2++] = L'/';
                for (int z = 0; z < bn; z++) g_pool[slot][o2++] = b[z];
                g_pool[slot][o2] = 0;
                LONG c = InterlockedIncrement(&g_dtHits);
                if (c <= 40) logfmt("[dt-dual] dual designation\n");
                return g_pool[slot];
            }
        }
    }

    /* ---------- 策略 3.2: 裸番号 ----------
     * 单位名只剩 "17th" "81st" 这种（OBs.csv 里没有 Suffix，
     * 显示名就是裸番号）。
     *
     * 必须带序数后缀。曾经这里直接用 conv_num_prefix，它接受纯数字，
     * 结果界面上所有数字都变成了番号 —— 飞机数量 218 显示成 "第218"。
     * 数量、回合数、坐标全是纯数字，一个都不能碰。 */
    {
        wchar_t ntmp[24];
        int nn = 0, okBare = 0;
        if (len >= 3) {
            wchar_t a = text[len - 2], b = text[len - 1];
            okBare = (a==L't'&&b==L'h') || (a==L's'&&b==L't') ||
                     (a==L'n'&&b==L'd') || (a==L'r'&&b==L'd');
        }
        /* 裸罗马数字也算番号（"CCXXXII" "XXXVIII"） */
        if (!okBare && len >= 2) {
            int rom = 1;
            for (int z = 0; z < len; z++) {
                wchar_t c = text[z];
                if (c != L'I' && c != L'V' && c != L'X' && c != L'L' &&
                    c != L'C' && c != L'D' && c != L'M') { rom = 0; break; }
            }
            okBare = rom;
        }
        if (okBare) nn = conv_num_prefix(text, len, ntmp, 24);
        if (nn > 0) {
            slot = (int)(InterlockedIncrement(&g_poolIdx) % POOL);
            memcpy(g_pool[slot], ntmp, (nn + 1) * sizeof(wchar_t));
            LONG c = InterlockedIncrement(&g_dtHits);
            if (c <= 40)
                logfmt("[dt-bare] bare designation -> %d wchars\n", nn);
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
            if (kl <= len || len < 12 || kl < 5) continue;
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

    if (g_noSeg) return text;

    /* ---------- 策略 9: 分段兜底 ----------
     * 拼出来的串（OOB 树、统计行、国家(城市) - 质量）永远整串命中不了。
     * 全或无地逐段翻译，有任何一个 token 译不出就整串放弃。 */
    {
        static wchar_t segBuf[POOL][512];
        static int segIdx = 0;
        wchar_t *slot = segBuf[segIdx++ & (POOL - 1)];
        wchar_t *r9 = seg_fill(text, slot, 512);
        if (r9 && wcscmp(r9, text) != 0) return r9;
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
     * 有了它们才能反查这个串是哪个界面画的 —— 不同界面显示的
     * 部队名形态不同，光看文本本身分不出来。 */
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

static wchar_t *dt_check_raw(wchar_t *text)
{
    wchar_t *r = dt_check_body(text);
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
     * 部队名等运行时拼接的串都是左对齐列表，本来就不需要位移。
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
    fclose(f);
    return EXCEPTION_CONTINUE_SEARCH;
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
    }

    logfmt("=== WITE2 中文化 v4 (内存改写) ===\n词典: %d 条 (.data 专用 %d 条)\n"
           "[patch] rdata=%d data=%d\n",
           g_ndic, g_nDataOnly, g_patchRdata, g_patchData);

    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) { logfmt("[fail] 无 exe 句柄\n"); return; }

    /* 崩因取证：必须在任何改写之前装好，否则崩在改写前就什么也拿不到。 */
    SetUnhandledExceptionFilter(crash_filter);
    logfmt("[crash] 异常过滤器已安装 -> wite2_crash.txt\n");

    g_patLog = fopen("wite2_patch.txt", "wb");
    patch_all(exe);
    if (g_patLog) { fclose(g_patLog); g_patLog = NULL; }

    logfmt("\n改写 %d 处, 译文过长跳过 %d 条, 未找到 %d 条\n",
           g_patched, g_tooLong, g_notFound);
}

static DWORD WINAPI init_thread(LPVOID param)
{
    /* .rdata 已在 DllMain 里改完。这里等游戏起来再装绘制层 hook，
     * 用来兜底运行时拼接的串（部队名、剧本名等）。 */
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

    return 0;
}

#ifdef ZH_SELFTEST
/* ===== 自测入口 =====
 *
 * 用 -DZH_SELFTEST 编译出来的 exe 直接从 stdin 读 UTF-8 行，
 * 走**真实的 C 匹配逻辑**，输出 "原文<TAB>译文<TAB>策略"。
 *
 * 存在的理由：tools/test_match.py 是 C 逻辑的 Python 复刻，两者会
 * 悄悄分叉。修 conv_num_prefix 边界那次就是 —— Python 用切片写，
 * C 用下标，C 错了 Python 全绿，直到游戏里看见 "79th步兵师" 才发现。
 * 以后验证一律以这个 exe 为准，Python 复刻只用来快速回归。
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
    if (argc > 1 && strcmp(argv[1], "--nosseg") == 0) g_noSeg = 1;
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