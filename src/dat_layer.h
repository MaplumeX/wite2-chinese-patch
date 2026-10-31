#ifndef DAT_LAYER_H
#define DAT_LAYER_H

/* ================= .dat 数据层翻译 =================
 *
 * 独立成模块的原因：这部分要能**离线测试**。钩子安装没法测（需要真实
 * 游戏进程），但解密/解压/查表/替换/重新打包这套逻辑全是纯函数，
 * 拆出来就能用真实 .dat 跑端到端验证。
 *
 * 依赖只有 KERNEL32 + CRT + inflate.c，不碰 hook_text.c 的任何全局量。
 */

#include <stdio.h>

/* 日志回调：传 NULL 则不记录。msg 已格式化好，带换行。 */
typedef void (*dat_log_fn)(const char *msg);
void dat_set_logger(dat_log_fn fn);

/* 载入映射表（TSV，UTF-8）。重复调用只生效一次。返回条目数。 */
int dat_load_map(const char *tsv_path);

/* 当前映射条目数 */
int dat_map_count(void);

/* 判断路径是否是我们接管的 GenData\*.dat。
 * 是则把文件名写进 out 并返回 1。 */
int dat_match_name(const char *path, char *out, int cap);

/* 在明文缓冲区里做等长替换，返回命中数。 */
int dat_apply(unsigned char *plain, unsigned long len);

/* 把打过补丁的 .dat 写成临时文件，返回路径（调用者 free）。NULL = 未接管。 */
char *dat_materialize(const char *path);

/* 完整重定向：读 path -> 解密 -> 解压 -> 替换 -> 压缩 -> 加密 -> tmpfile。
 * impl 是游戏原生的 fopen 实现（path, -1, mode）。
 * 返回 NULL 表示未接管（无映射 / 路径不匹配 / 任何一步失败），
 * 调用者应回退到原生 fopen，行为零变化。
 */
FILE *dat_redirect(const char *path,
                   FILE *(*impl)(const char *, int, const char *));

/* 释放映射表 */
void dat_free_map(void);

#endif
