/* .dat 数据层：解密(RC4) + 解压(gzip) + 等长替换 + 重新打包
 *
 * 见 dat_layer.h 的说明。这里只放纯逻辑，不碰钩子。
 */

#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <stdarg.h>
#include <ctype.h>

#include "dat_layer.h"

int gz_decompress(const unsigned char *in, size_t inlen,
                  unsigned char **out, size_t *outlen);
int gz_compress_stored(const unsigned char *in, size_t inlen,
                       unsigned char **out, size_t *outlen);

static dat_log_fn g_logFn = NULL;
void dat_set_logger(dat_log_fn fn) { g_logFn = fn; }

static void dat_log(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    if (!g_logFn) return;
    va_start(ap, fmt);
    _vsnprintf(buf, sizeof(buf) - 2, fmt, ap);
    va_end(ap);
    g_logFn(buf);
}

/* ================= .dat 数据层翻译（运行时现场生成） =================
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
 * 时现场解密、替换、重新打包，用 tmpfile() 返回一个匿名临时文件。
 * 游戏拿到的是普通 FILE*，fread/fclose 全部照常，它看不出区别。
 *
 * 好处：Steam 校验通过；更新后原文件是最新的，我们的映射表还在，
 * 匹配得上的条目自动生效，匹配不上的退回英文 —— 最坏情况只是不翻译。
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
 * JMP 直接盖住序言即可，**不需要 trampoline** —— 未命中时我们直接按原
 * 逻辑调 0x85F280。
 */

/* 16 字节密钥 = MD5("W17VVI$G0ODG4M3")。
 * 游戏用 CryptoAPI 算 MD5，但结果固定，所以直接硬编码，省掉一份 MD5 实现。 */
static const unsigned char DAT_KEY[16] = {
    0xaa, 0x5c, 0x3f, 0xa3, 0xe3, 0x61, 0x25, 0xa2,
    0x54, 0xb9, 0xaa, 0x8a, 0x54, 0x52, 0x18, 0xb7
};

typedef FILE *(__cdecl *pFopenImpl)(const char *path, int flag,
                                    const char *mode);
static pFopenImpl g_fopenImpl = NULL;

/* 一条映射：原文（UTF-16）-> 译文（UTF-16），长度均按 wchar 计 */
typedef struct {
    wchar_t *wkey;
    int klen;
    wchar_t *wval;
    int vlen;
} DatEntry;

static DatEntry *g_dat = NULL;
static int g_datN = 0, g_datCap = 0;
static int g_datTried = 0;

/* 哪些 .dat 文件有映射。启动时从 `#@files` 行读进来。
 *
 * 为什么需要：没有映射的 .dat 如果也解密+解压一遍，启动时要白解
 * 52 MB（w_unit 12 MB + w_hex 10 MB + …），白卡 1~2 秒。
 * 比一下文件名就跳过，零成本。 */
static char g_datFiles[512];      /* 逗号分隔，全小写 */

static int dat_file_wanted(const char *name)
{
    const char *p;
    int i, j, n;
    if (!g_datFiles[0]) return 1;      /* 没声明就全试 */
    n = (int)strlen(name);
    p = g_datFiles;
    while (*p) {
        const char *q = p;
        while (*q && *q != ',') q++;
        if ((int)(q - p) == n) {
            for (i = 0, j = 0; i < n; i++) {
                char a = (char)tolower((unsigned char)name[i]);
                char b = (char)tolower((unsigned char)p[i]);
                if (a != b) break;
                j++;
            }
            if (j == n) return 1;
        }
        p = (*q == ',') ? q + 1 : q;
    }
    return 0;
}

/* 与 Python 侧 dat_scan.find_utf16_strings 一致的码点白名单。
 * 不一致会导致同一串在两边判定不同，表现为“映射表里有但运行时匹配不上”。 */
static int dat_cp_ok(unsigned int c)
{
    return ((c >= 32 && c < 0x7f)
            || (c >= 0xa0 && c <= 0x24f)
            || (c >= 0x2010 && c <= 0x201f)
            || (c >= 0x3000 && c <= 0x303f)
            || (c >= 0x4e00 && c <= 0x9fff)
            || (c >= 0xff00 && c <= 0xffef));
}

static void dat_rc4(unsigned char *buf, size_t n)
{
    unsigned char S[256];
    size_t i;
    int j = 0, x = 0, y = 0;
    for (i = 0; i < 256; i++) S[i] = (unsigned char)i;
    for (i = 0; i < 256; i++) {
        unsigned char t;
        j = (j + S[i] + DAT_KEY[i & 15]) & 0xFF;
        t = S[i]; S[i] = S[j]; S[j] = t;
    }
    for (i = 0; i < n; i++) {
        unsigned char t;
        x = (x + 1) & 0xFF;
        y = (y + S[x]) & 0xFF;
        t = S[x]; S[x] = S[y]; S[y] = t;
        buf[i] ^= S[(S[x] + S[y]) & 0xFF];
    }
}

/* 读 wite2_dat.tsv（UTF-8，`原文<TAB>译文`）。
 * 以 '@' 开头的行需要反转义 \t \n \\（和 wite2_zh.tsv 同一套约定）。 */
int dat_load_map(const char *tsv_path)
{
    FILE *f;
    char *buf;
    long sz;
    char *p, *end;

    if (g_datTried) return g_datN;
    g_datTried = 1;

    f = fopen(tsv_path, "rb");
    if (!f) { dat_log("[dat] 映射表 %s 不存在，跳过数据层\n", tsv_path); return 0; }
    fseek(f, 0, SEEK_END);
    sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0 || sz > 32 * 1024 * 1024) { fclose(f); return 0; }
    buf = (char *)malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return 0; }
    if (fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        free(buf); fclose(f); return 0;
    }
    fclose(f);
    buf[sz] = 0;

    p = buf;
    end = buf + sz;
    while (p < end) {
        char *nl = p, *tab, *lineEnd;
        char *k, *v;
        char kbuf[8192], vbuf[8192];
        int kesc = 0, ki = 0, vi = 0;
        wchar_t *wk, *wv;
        int wkn, wvn;

        while (nl < end && *nl != '\n') nl++;
        lineEnd = nl;
        if (lineEnd > p && lineEnd[-1] == '\r') lineEnd--;
        if (lineEnd <= p) { p = nl + 1; continue; }
        /* 文件清单行：#@files a.dat,b.dat */
        if (p[0] == '#' && p[1] == '@' && lineEnd - p > 7
            && memcmp(p + 2, "files", 5) == 0) {
            char *q = p + 7;
            int o = 0;
            while (q < lineEnd && *q == ' ') q++;
            while (q < lineEnd && o < (int)sizeof(g_datFiles) - 1)
                g_datFiles[o++] = *q++;
            g_datFiles[o] = 0;
            dat_log("[dat] 映射目标文件: %s\n", g_datFiles);
            p = nl + 1;
            continue;
        }
        if (*p == '#') { p = nl + 1; continue; }

        tab = p;
        while (tab < lineEnd && *tab != '\t') tab++;
        if (tab >= lineEnd) { p = nl + 1; continue; }

        k = p; v = tab + 1;
        if (*k == '@') { kesc = 1; k++; }

        /* 反转义 */
        while (k < tab && ki < (int)sizeof(kbuf) - 2) {
            if (kesc && *k == '\\' && k + 1 < tab) {
                k++;
                kbuf[ki++] = (*k == 't') ? '\t' : (*k == 'n') ? '\n' : *k;
            } else kbuf[ki++] = *k;
            k++;
        }
        kbuf[ki] = 0;
        while (v < lineEnd && vi < (int)sizeof(vbuf) - 2) {
            if (kesc && *v == '\\' && v + 1 < lineEnd) {
                v++;
                vbuf[vi++] = (*v == 't') ? '\t' : (*v == 'n') ? '\n' : *v;
            } else vbuf[vi++] = *v;
            v++;
        }
        vbuf[vi] = 0;

        wkn = MultiByteToWideChar(CP_UTF8, 0, kbuf, ki, NULL, 0);
        wvn = MultiByteToWideChar(CP_UTF8, 0, vbuf, vi, NULL, 0);
        if (wkn <= 0 || wvn <= 0) { p = nl + 1; continue; }

        wk = (wchar_t *)malloc(((size_t)wkn + 1) * sizeof(wchar_t));
        wv = (wchar_t *)malloc(((size_t)wvn + 1) * sizeof(wchar_t));
        if (!wk || !wv) { free(wk); free(wv); p = nl + 1; continue; }
        MultiByteToWideChar(CP_UTF8, 0, kbuf, ki, wk, wkn);
        MultiByteToWideChar(CP_UTF8, 0, vbuf, vi, wv, wvn);
        wk[wkn] = 0; wv[wvn] = 0;

        if (g_datN >= g_datCap) {
            int nc = g_datCap ? g_datCap * 2 : 64;
            DatEntry *nd = (DatEntry *)realloc(g_dat, (size_t)nc * sizeof(DatEntry));
            if (!nd) { free(wk); free(wv); break; }
            g_dat = nd; g_datCap = nc;
        }
        g_dat[g_datN].wkey = wk;
        g_dat[g_datN].klen = wkn;
        g_dat[g_datN].wval = wv;
        g_dat[g_datN].vlen = wvn;
        g_datN++;

        p = nl + 1;
    }
    free(buf);
    dat_log("[dat] 映射表 %d 条\n", g_datN);
    return g_datN;
}

/* 在明文里做等长替换。返回命中数。
 *
 * 等长是关键：明文是长度前缀结构，改长度会移动后面所有字节，
 * 而文件里还有记录偏移/计数字段，一移就整体错位。
 * 中文比英文短，补空格绰绰有余；补 L' ' 而不是 memset(' ')，
 * 否则会写出 U+2020 而不是 U+0020。
 */
int dat_apply(unsigned char *plain, unsigned long len)
{
    size_t i = 0x100;            /* 跳过 _2by3 WITE2_SF 头 */
    int hits = 0;

    if (i & 1) i++;
    while (i + 3 < len) {
        size_t j = i;
        int nch = 0;
        while (j + 1 < len) {
            unsigned int c = (unsigned int)plain[j]
                           | ((unsigned int)plain[j + 1] << 8);
            if (c == 0 || !dat_cp_ok(c)) break;
            nch++; j += 2;
        }
        if (nch >= 3) {
            int k;
            for (k = 0; k < g_datN; k++) {
                if (g_dat[k].klen != nch) continue;
                if (memcmp(plain + i, g_dat[k].wkey,
                           (size_t)nch * sizeof(wchar_t)) != 0) continue;
                if (g_dat[k].vlen > nch) break;   /* 过长，跳过 */
                {
                    wchar_t *dst = (wchar_t *)(void *)(plain + i);
                    int z;
                    memcpy(dst, g_dat[k].wval,
                           (size_t)g_dat[k].vlen * sizeof(wchar_t));
                    for (z = g_dat[k].vlen; z < nch; z++) dst[z] = L' ';
                }
                hits++;
                break;
            }
            i = (j > i) ? j : i + 2;
        } else {
            i += 2;
        }
    }
    return hits;
}

int dat_map_count(void) { return g_datN; }

/* 判断路径是不是我们关心的 GenData\*.dat，是则取出文件名 */
int dat_match_name(const char *path, char *out, int cap)
{
    const char *b = path, *p;
    int n, hasGen = 0;

    if (!path) return 0;
    for (p = path; *p; p++)
        if (*p == '\\' || *p == '/') b = p + 1;
    n = (int)strlen(b);
    if (n < 5 || n >= cap) return 0;
    if (_stricmp(b + n - 4, ".dat") != 0) return 0;

    /* 必须来自 GenData 目录：别的 .dat 可能格式不同，不能乱改 */
    for (p = path; p + 7 <= b; p++) {
        if (_strnicmp(p, "gendata", 7) == 0) { hasGen = 1; break; }
    }
    if (!hasGen) return 0;

    memcpy(out, b, (size_t)n);
    out[n] = 0;
    return 1;
}

/* 完整流程：读原文件 -> 解密 -> 解压 -> 替换 -> 压缩 -> 加密 -> tmpfile()
 * 任何一步失败都返回 NULL，调用者回退到原生 fopen，行为零变化。
 */
FILE *dat_redirect(const char *path, FILE *(*impl)(const char*,int,const char*))
{
    char name[128];
    FILE *src, *out;
    unsigned char *blob = NULL, *plain = NULL, *packed = NULL;
    long sz;
    size_t plen = 0, pklen = 0;
    int hits;

    if (!impl) return NULL;
    if (!dat_match_name(path, name, sizeof(name))) return NULL;
    if (!dat_file_wanted(name)) return NULL;   /* 这个文件没映射，直接放行 */

    /* 映射表由调用方在启动时用 dat_load_map(路径) 载入；
     * 这里不再自己载入，因为本模块不知道游戏目录在哪。 */
    if (g_datN <= 0) return NULL;

    src = impl(path, -1, "rb");
    if (!src) return NULL;
    fseek(src, 0, SEEK_END);
    sz = ftell(src);
    fseek(src, 0, SEEK_SET);
    if (sz <= 0 || sz > 128 * 1024 * 1024) { fclose(src); return NULL; }

    blob = (unsigned char *)malloc((size_t)sz);
    if (!blob) { fclose(src); return NULL; }
    if (fread(blob, 1, (size_t)sz, src) != (size_t)sz) {
        free(blob); fclose(src); return NULL;
    }
    fclose(src);

    dat_rc4(blob, (size_t)sz);
    if (gz_decompress(blob, (size_t)sz, &plain, &plen) != 0) {
        dat_log("[dat] %s 解压失败\n", name);
        free(blob);
        return NULL;
    }
    free(blob);

    hits = dat_apply(plain, (unsigned long)plen);
    if (hits <= 0) { free(plain); return NULL; }

    if (gz_compress_stored(plain, plen, &packed, &pklen) != 0) {
        free(plain);
        return NULL;
    }
    free(plain);
    dat_rc4(packed, pklen);

    out = tmpfile();
    if (!out) { free(packed); return NULL; }
    if (fwrite(packed, 1, pklen, out) != pklen) {
        fclose(out); free(packed); return NULL;
    }
    free(packed);
    rewind(out);

    dat_log("[dat] %s 命中 %d 处，明文 %u B -> 密文 %u B\n",
           name, hits, (unsigned)plen, (unsigned)pklen);
    return out;
}



/* 把打过补丁的 .dat 落到临时文件，返回该文件路径（调用者负责 free）。
 *
 * 为什么落临时文件而不是直接用内存
 * -------------------------------
 * 拦截点是 CreateFile：调用者拿到的是 HANDLE，之后自己 ReadFile。
 * 要让游戏读到补丁内容，最干净的做法就是把补丁写成一个真实文件，
 * 再把路径换掉 —— 这样不管它后面用 ReadFile、内存映射还是别的，
 * 全部自然生效，我们不用去模拟文件对象。
 *
 * 用 %TEMP% 下的唯一文件名，不碰游戏目录。返回 NULL 表示未接管。
 */
char *dat_materialize(const char *path)
{
    char name[128];
    char tmpdir[MAX_PATH];
    char *outpath;
    FILE *src, *out;
    unsigned char *blob = NULL, *plain = NULL, *packed = NULL;
    long sz;
    size_t plen = 0, pklen = 0;
    int hits;

    if (!dat_match_name(path, name, sizeof(name))) return NULL;
    if (!dat_file_wanted(name)) return NULL;
    if (g_datN <= 0) return NULL;

    src = fopen(path, "rb");
    if (!src) return NULL;
    fseek(src, 0, SEEK_END);
    sz = ftell(src);
    fseek(src, 0, SEEK_SET);
    if (sz <= 0 || sz > 128 * 1024 * 1024) { fclose(src); return NULL; }

    blob = (unsigned char *)malloc((size_t)sz);
    if (!blob) { fclose(src); return NULL; }
    if (fread(blob, 1, (size_t)sz, src) != (size_t)sz) {
        free(blob); fclose(src); return NULL;
    }
    fclose(src);

    dat_rc4(blob, (size_t)sz);
    if (gz_decompress(blob, (size_t)sz, &plain, &plen) != 0) {
        dat_log("[dat] %s 解压失败，跳过\n", name);
        free(blob);
        return NULL;
    }
    free(blob);

    hits = dat_apply(plain, (unsigned long)plen);
    if (hits <= 0) { free(plain); return NULL; }

    if (gz_compress_stored(plain, plen, &packed, &pklen) != 0) {
        free(plain);
        return NULL;
    }
    free(plain);
    dat_rc4(packed, pklen);

    if (!GetTempPathA(sizeof(tmpdir), tmpdir)) {
        free(packed);
        return NULL;
    }
    outpath = (char *)malloc(MAX_PATH);
    if (!outpath) { free(packed); return NULL; }
    if (!GetTempFileNameA(tmpdir, "w2z", 0, outpath)) {
        free(outpath); free(packed); return NULL;
    }

    out = fopen(outpath, "wb");
    if (!out) { free(outpath); free(packed); return NULL; }
    if (fwrite(packed, 1, pklen, out) != pklen) {
        fclose(out); remove(outpath); free(outpath); free(packed);
        return NULL;
    }
    fclose(out);
    free(packed);

    dat_log("[dat] %s 命中 %d 处，%u B 明文 -> 临时文件 %s\n",
            name, hits, (unsigned)plen, outpath);
    return outpath;
}

void dat_free_map(void)
{
    int i;
    for (i = 0; i < g_datN; i++) {
        free(g_dat[i].wkey);
        free(g_dat[i].wval);
    }
    free(g_dat);
    g_dat = NULL;
    g_datN = g_datCap = 0;
    g_datTried = 0;
}
