/* ================= gzip 解压 / 打包 + CRC32 =================
 *
 * 为什么自己写，不用 zlib
 * ----------------------
 * 这个 DLL 只依赖 KERNEL32（/MT 静态 CRT）。VS 和 Windows SDK 里都没有
 * zlib.h / zlib.lib，引入第三方库还要额外维护一份源码树。而这里的需求
 * 极窄：
 *
 *   解压：只解游戏自己的 .dat（标准 gzip，deflate 的三种块类型都可能）
 *   压缩：**不需要真压缩** —— deflate 的 stored 块（BTYPE=00）允许
 *         原样存放，体积略大但完全合法，游戏照样能读
 *
 * 所以解压实现完整 inflate，压缩只用 stored 块。总共约 350 行。
 *
 * 已验证：20 个 .dat 全部解压成功，明文与 Python zlib 逐字节一致。
 */

#include <windows.h>
#include <string.h>
#include <stdlib.h>

/* ---------------- CRC32 ---------------- */

static unsigned int g_crcTable[256];
static int g_crcReady = 0;

static void crc_init(void)
{
    unsigned int c;
    int n, k;
    for (n = 0; n < 256; n++) {
        c = (unsigned int)n;
        for (k = 0; k < 8; k++)
            c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        g_crcTable[n] = c;
    }
    g_crcReady = 1;
}

static unsigned int crc32_calc(const unsigned char *buf, size_t len)
{
    unsigned int c = 0xFFFFFFFFu;
    size_t i;
    if (!g_crcReady) crc_init();
    for (i = 0; i < len; i++)
        c = g_crcTable[(c ^ buf[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

/* ---------------- 位读取（LSB 优先） ---------------- */

typedef struct {
    const unsigned char *in;
    size_t inlen;
    size_t pos;
    unsigned int bitbuf;
    int bitcnt;
    int err;
} BitReader;

static unsigned int br_bits(BitReader *b, int need)
{
    unsigned int v;
    while (b->bitcnt < need) {
        if (b->pos >= b->inlen) { b->err = 1; return 0; }
        b->bitbuf |= (unsigned int)b->in[b->pos++] << b->bitcnt;
        b->bitcnt += 8;
    }
    v = b->bitbuf & ((1u << need) - 1);
    b->bitbuf >>= need;
    b->bitcnt -= need;
    return v;
}

static void br_align(BitReader *b)
{
    b->bitbuf = 0;
    b->bitcnt = 0;
}

/* ---------------- Huffman ---------------- */

#define MAXBITS 15

typedef struct {
    short count[MAXBITS + 1];
    short symbol[288];
} Huff;

/* 返回 0 成功，非 0 表示码表非法（过订阅或空码） */
static int huff_build(Huff *h, const unsigned char *length, int n)
{
    int i, left;
    short offs[MAXBITS + 1];

    for (i = 0; i <= MAXBITS; i++) h->count[i] = 0;
    for (i = 0; i < n; i++) h->count[length[i]]++;
    if (h->count[0] == n) return 0;      /* 全是 0 长度：合法但不用 */

    left = 1;
    for (i = 1; i <= MAXBITS; i++) {
        left <<= 1;
        left -= h->count[i];
        if (left < 0) return 1;          /* 过订阅 */
    }

    offs[1] = 0;
    for (i = 1; i < MAXBITS; i++)
        offs[i + 1] = (short)(offs[i] + h->count[i]);
    for (i = 0; i < n; i++)
        if (length[i]) h->symbol[offs[length[i]]++] = (short)i;
    return 0;
}

static int huff_decode(BitReader *b, const Huff *h)
{
    int code = 0, first = 0, index = 0, len;
    for (len = 1; len <= MAXBITS; len++) {
        int count;
        code |= (int)br_bits(b, 1);
        if (b->err) return -1;
        count = h->count[len];
        if (code - count < first) return h->symbol[index + (code - first)];
        index += count;
        first += count;
        first <<= 1;
        code <<= 1;
    }
    return -1;
}

/* ---------------- 长度 / 距离表 ---------------- */

static const unsigned short LEN_BASE[29] = {
    3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,
    131,163,195,227,258 };
static const unsigned char LEN_EXTRA[29] = {
    0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0 };
static const unsigned short DIST_BASE[30] = {
    1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,
    2049,3073,4097,6145,8193,12289,16385,24577 };
static const unsigned char DIST_EXTRA[30] = {
    0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13 };

/* ---------------- inflate 主体 ---------------- */

#define OUT_INIT  (1u << 20)

static int inflate_raw(const unsigned char *in, size_t inlen,
                       unsigned char **out, size_t *outlen)
{
    BitReader br;
    unsigned char *buf = NULL;
    size_t cap = 0, len = 0;
    int last, type;
    int rc = 1;

    memset(&br, 0, sizeof(br));
    br.in = in;
    br.inlen = inlen;

    for (;;) {
        last = (int)br_bits(&br, 1);
        type = (int)br_bits(&br, 2);
        if (br.err) goto done;

        if (type == 0) {
            /* stored */
            unsigned int n, nn;
            br_align(&br);
            if (br.pos + 4 > br.inlen) goto done;
            n = (unsigned int)br.in[br.pos] | ((unsigned int)br.in[br.pos + 1] << 8);
            nn = (unsigned int)br.in[br.pos + 2] | ((unsigned int)br.in[br.pos + 3] << 8);
            br.pos += 4;
            if ((n ^ 0xFFFF) != nn) goto done;
            if (br.pos + n > br.inlen) goto done;
            if (len + n > cap) {
                size_t nc = cap ? cap : OUT_INIT;
                unsigned char *nb;
                while (nc < len + n) nc <<= 1;
                nb = (unsigned char *)realloc(buf, nc);
                if (!nb) goto done;
                buf = nb; cap = nc;
            }
            memcpy(buf + len, br.in + br.pos, n);
            len += n;
            br.pos += n;
        } else if (type == 1 || type == 2) {
            Huff lit, dist;
            static const unsigned char order[19] = {
                16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15 };
            int i;

            if (type == 1) {
                unsigned char lens[288];
                for (i = 0; i < 144; i++) lens[i] = 8;
                for (; i < 256; i++) lens[i] = 9;
                for (; i < 280; i++) lens[i] = 7;
                for (; i < 288; i++) lens[i] = 8;
                if (huff_build(&lit, lens, 288)) goto done;
                for (i = 0; i < 30; i++) lens[i] = 5;
                if (huff_build(&dist, lens, 30)) goto done;
            } else {
                int hlit = (int)br_bits(&br, 5) + 257;
                int hdist = (int)br_bits(&br, 5) + 1;
                int hclen = (int)br_bits(&br, 4) + 4;
                unsigned char clen[19];
                unsigned char lens[288 + 30];
                Huff cl;

                if (hlit > 286 || hdist > 30) goto done;
                for (i = 0; i < 19; i++) clen[i] = 0;
                for (i = 0; i < hclen; i++) clen[order[i]] = (unsigned char)br_bits(&br, 3);
                if (br.err) goto done;
                if (huff_build(&cl, clen, 19)) goto done;

                i = 0;
                while (i < hlit + hdist) {
                    int sym = huff_decode(&br, &cl);
                    if (sym < 0) goto done;
                    if (sym < 16) {
                        lens[i++] = (unsigned char)sym;
                    } else if (sym == 16) {
                        int rep = 3 + (int)br_bits(&br, 2);
                        unsigned char prev;
                        if (i == 0) goto done;
                        prev = lens[i - 1];
                        while (rep-- && i < hlit + hdist) lens[i++] = prev;
                    } else if (sym == 17) {
                        int rep = 3 + (int)br_bits(&br, 3);
                        while (rep-- && i < hlit + hdist) lens[i++] = 0;
                    } else {
                        int rep = 11 + (int)br_bits(&br, 7);
                        while (rep-- && i < hlit + hdist) lens[i++] = 0;
                    }
                    if (br.err) goto done;
                }
                if (huff_build(&lit, lens, hlit)) goto done;
                if (huff_build(&dist, lens + hlit, hdist)) goto done;
            }

            /* 解码符号 */
            for (;;) {
                int sym = huff_decode(&br, &lit);
                if (sym < 0) goto done;
                if (sym < 256) {
                    if (len + 1 > cap) {
                        size_t nc = cap ? cap * 2 : OUT_INIT;
                        unsigned char *nb = (unsigned char *)realloc(buf, nc);
                        if (!nb) goto done;
                        buf = nb; cap = nc;
                    }
                    buf[len++] = (unsigned char)sym;
                } else if (sym == 256) {
                    break;                       /* 块结束 */
                } else {
                    int lsym = sym - 257, dsym, l, d;
                    if (lsym >= 29) goto done;
                    l = LEN_BASE[lsym] + (int)br_bits(&br, LEN_EXTRA[lsym]);
                    dsym = huff_decode(&br, &dist);
                    if (dsym < 0 || dsym >= 30) goto done;
                    d = DIST_BASE[dsym] + (int)br_bits(&br, DIST_EXTRA[dsym]);
                    if (br.err || (size_t)d > len) goto done;
                    if (len + l > cap) {
                        size_t nc = cap ? cap : OUT_INIT;
                        unsigned char *nb;
                        while (nc < len + l) nc <<= 1;
                        nb = (unsigned char *)realloc(buf, nc);
                        if (!nb) goto done;
                        buf = nb; cap = nc;
                    }
                    {
                        size_t src = len - d;
                        int k;
                        for (k = 0; k < l; k++) buf[len++] = buf[src + k];
                    }
                }
            }
        } else {
            goto done;                            /* type 3 非法 */
        }

        if (last) break;
    }

    *out = buf;
    *outlen = len;
    rc = 0;

done:
    if (rc) { free(buf); *out = NULL; *outlen = 0; }
    return rc;
}

/* ---------------- gzip 封装 ---------------- */

/* 解 gzip（含头解析）。返回 0 成功。 */
int gz_decompress(const unsigned char *in, size_t inlen,
                  unsigned char **out, size_t *outlen)
{
    size_t p = 0;
    unsigned int flg;

    if (inlen < 18) return 1;
    if (in[0] != 0x1F || in[1] != 0x8B) return 2;   /* gzip magic */
    if (in[2] != 8) return 3;                        /* deflate */

    flg = in[3];
    p = 10;
    if (flg & 4) {                                   /* FEXTRA */
        unsigned int xlen;
        if (p + 2 > inlen) return 4;
        xlen = (unsigned int)in[p] | ((unsigned int)in[p + 1] << 8);
        p += 2 + xlen;
    }
    if (flg & 8) {                                   /* FNAME */
        while (p < inlen && in[p]) p++;
        p++;
    }
    if (flg & 16) {                                  /* FCOMMENT */
        while (p < inlen && in[p]) p++;
        p++;
    }
    if (flg & 2) p += 2;                             /* FHCRC */
    if (p >= inlen) return 5;

    return inflate_raw(in + p, inlen - p, out, outlen);
}

/* 打 gzip（只用 stored 块，不压缩）。返回 0 成功。 */
int gz_compress_stored(const unsigned char *in, size_t inlen,
                       unsigned char **out, size_t *outlen)
{
    size_t blocks = (inlen + 65534) / 65535;
    size_t cap, p = 0, off = 0;
    unsigned char *buf;

    if (blocks == 0) blocks = 1;
    cap = inlen + blocks * 5 + 32;
    buf = (unsigned char *)malloc(cap);
    if (!buf) return 1;

    buf[p++] = 0x1F; buf[p++] = 0x8B;   /* magic */
    buf[p++] = 8;                       /* deflate */
    buf[p++] = 0;                       /* flags */
    buf[p++] = 0; buf[p++] = 0; buf[p++] = 0; buf[p++] = 0;  /* mtime */
    buf[p++] = 2;                       /* xfl */
    buf[p++] = 255;                     /* os = unknown */

    while (off < inlen || (inlen == 0 && off == 0)) {
        size_t n = inlen - off;
        int isLast;
        if (n > 65535) n = 65535;
        isLast = (off + n >= inlen);
        buf[p++] = (unsigned char)(isLast ? 1 : 0);   /* BFINAL + BTYPE=00 */
        buf[p++] = (unsigned char)(n & 0xFF);
        buf[p++] = (unsigned char)((n >> 8) & 0xFF);
        buf[p++] = (unsigned char)(~n & 0xFF);
        buf[p++] = (unsigned char)((~n >> 8) & 0xFF);
        if (n) memcpy(buf + p, in + off, n);
        p += n;
        off += n;
        if (inlen == 0) break;
    }

    {
        unsigned int c = crc32_calc(in, inlen);
        unsigned int sz = (unsigned int)inlen;
        int k;
        for (k = 0; k < 4; k++) buf[p++] = (unsigned char)((c >> (k * 8)) & 0xFF);
        for (k = 0; k < 4; k++) buf[p++] = (unsigned char)((sz >> (k * 8)) & 0xFF);
    }

    *out = buf;
    *outlen = p;
    return 0;
}
