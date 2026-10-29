# War in the East 2 中文化补丁

给《Gary Grigsby's War in the East 2》游戏界面加中文显示的注入式补丁。

**不改游戏 exe**，只放一个代理 DLL 到游戏目录，随时可删除还原。

---

## 当前状态

| 项目 | 状态 |
|---|---|
| 中文字形显示 | ✅ 可用 |
| 界面文本替换 | ✅ 可用（含工具提示） |
| 排版对齐 | ✅ 自动正确 |
| 抗游戏更新 | ✅ 只需补翻新增条目 |
| 翻译进度 | ⚠️ 仅 65 条（主菜单 + 常用术语） |

**这是一个技术验证可用的框架，不是完整汉化。** 翻译量还很小，欢迎补充词条。

---

## 原理

### 难点一：字形

游戏引擎用 GDI 把字光栅化进 D3D9 纹理图集，走的是：

```
CreateFontW → GetFontUnicodeRanges → 逐字符 ExtTextOutW → 上传纹理
```

直接用系统里装的中文字体会卡死：微软雅黑声明了 **29905 个码位**，引擎会试图全部光栅化，
47 个字体实例 × 上万字形 → 图集爆掉。

**解法：字体子集化。**

用 `fontTools` 只生成包含"实际用到的字"的字体，并把内部 family name 改成唯一名
（`WITE2ZH`），避免引擎去加载系统里那份完整字体。

```
29905 码位 → 187~256 码位
19 MB      → 65~110 KB
```

日志验证：

```
FONT WITE2ZH size -19 : 256 glyphs, 1 pages (512x512), 512 KB atlas
FONT TOTAL : 62 fonts, 8448 glyphs, 37 pages, ~18 MB atlas
```

### 难点二：文本在哪

游戏 exe 的 `.text` 段在磁盘上是**加密的**（熵 8.000），静态分析找不到任何函数。
但运行时内存中是解密的（熵 6.77），可以 dump 出来分析。

界面文本本身是 **UTF-16 明文**，全部躺在 `.rdata` 里，例如：

```
0x5bb3f0  "Game Options"
0x5bb9b8  "Set to human controlled Axis player."
```

### 难点三：只 hook 一个函数不够

游戏里至少有 **三个** 文本绘制函数：

| 函数 | RVA | 用途 |
|---|---|---|
| `DrawText` | `0x45CE60` | 主菜单标签 |
| `MeasureText` | `0x45D720` | 只测宽度 |
| 其他 | 未定位 | 工具提示、报告表格等 |

只 hook `DrawText` 的结果是：主菜单变中文了，但鼠标悬停的工具提示还是英文。

**最终解法：直接改写内存里的 `.rdata` 字符串。**

启动时把英文原地覆盖成中文，之后**任何**代码路径读到的都是中文，不用管它用哪个绘制函数。

额外好处：调用方测量宽度时读到的也是中文，**排版自动正确**，不需要修 x 坐标。

### 难点四：性能

v4 最初还在 `DrawText` 上挂了一个兜底 hook，用来处理运行时拼接的文本。
它每次绘制都对每个字符调一次 `VirtualQuery` 检查可读性 —— 一帧几千次系统调用，
导致明显掉帧。

而日志显示这个兜底 hook **零命中**（`.rdata` 改写已覆盖全部文本），纯属白跑。
v4.1 直接删掉，现在**运行时零开销**：只在启动时改一次内存，之后不拦截任何调用。

中文在 UTF-16 下远比英文短，原地覆盖空间充足：

```
"Game Options"                          26 字节  →  "游戏选项"      10 字节
"Set to human controlled Axis player."  72 字节  →  "设为玩家控制轴心"  18 字节
```

---

## 使用方法

### 1. 安装

```bash
# 编译（需要 Visual Studio 2019/2022 + C++ 桌面开发）
build.bat

# 生成字体和翻译表，并部署到游戏目录
python scripts/build_all.py
```

或者手动：

1. 把 `build/version.dll` 复制到游戏根目录
2. 把 `wite2_zh.tsv` 复制到游戏根目录
3. 运行 `python scripts/build_all.py` 生成 `Dat/Fonts/wite2_zh.ttf` 并改好 `Dat/GuiFonts.json`

### 2. 卸载

删除游戏目录下的：

```
version.dll
wite2_zh.tsv
wite2_cfg.ini
```

然后运行：

```bash
python scripts/build_all.py --restore
```

还原 `Dat/GuiFonts.json` 和 `Dat/FontConfig.txt`。

---

## 为什么用 `version.dll`

游戏 exe 静态导入 `VERSION.dll` 的 3 个函数：

```
VerQueryValueA
GetFileVersionInfoA
GetFileVersionInfoSizeA
```

而 `version.dll` **不在** Windows KnownDLLs 列表里，所以应用目录优先。
系统 `version.dll` 只有 17 个导出，全部转发很干净。

（`d3d9.dll` 也可以，但要转发 30+ 个导出；`winmm.dll` 导出更多。）

---

## 抗更新

| 游戏更新时 | 结果 |
|---|---|
| exe 被 Steam 覆盖 | 无影响，补丁没改过它 |
| `version.dll` 是我们的 | Steam 清单里没这个文件，不会动 |
| `wite2_zh.tsv` | 独立文件，不会动 |
| 需要重做吗 | **不需要**，只需补翻新增文本 |

更新后跑一次：

```bash
python scripts/build_all.py --diff
```

它会对比最新提取的英文表和已有译文，列出待翻的增量条目。

---

## 翻译词条

`zh_dict.json` 是翻译源文件，格式：

```json
{
  "Game Options": "游戏选项",
  "Supply": "补给",
  "End Turn": "结束回合"
}
```

英文 Key 必须与游戏里的字符串**完全一致**（区分大小写，包含标点）。
改完运行 `python scripts/build_all.py` 重新生成 `.tsv` 和字体。

### 已翻译（65 条）

- 主菜单全部条目
- 对战方设置
- 常用术语（补给、战斗、移动、补给站、空军、回合…）

### 待翻译

```
python scripts/build_all.py --diff
```

会生成 `todo.txt`。注意里面混有大量**玩家看不到的内部日志**
（`at DrawText Lock failure`、`InitDeviceObjects()` 等），不必全翻。

---

## 已知限制

1. **译文不能比原文长**
   内存改写是原地覆盖，中文字数超过英文字符数时会跳过该条（保留英文）。
   实际很少遇到，因为中文普遍更短。

2. **数据文本走另一条路**
   部队番号、将领名、装备名等在 `Dat/GenData/*.dat` 里，是**加密**的。
   游戏自带 CSV 导出/导入，理论上可以走编辑器流程，但未验证。

3. **可能影响内部逻辑**
   像 `None`、`Auto`、`ACTIVE` 这类短词如果被用于程序内部判断，
   改写后可能出问题。发现异常就把该条从 `zh_dict.json` 删掉。

4. **需要 32 位编译器**
   游戏是 x86 程序，DLL 必须 `-MACHINE:X86`。

---

## 目录结构

```
src/hook_text.c        注入 DLL 源码（代理 + 内存改写 + 兜底 hook）
src/version.def        17 个导出转发
tools/dump_code.c      诊断工具：dump 解密后的代码段（逆向用）
tools/extract_strings.py  从 exe 提取 UTF-16 界面文本
tools/make_subset_font.py 字体子集化
scripts/build_all.py   一键构建
zh_dict.json           翻译源文件
wite2_zh.tsv           生成的翻译表（部署到游戏目录）
build.bat              编译脚本
```

---

## 调试

`wite2_hook.txt` 会记录改写情况：

```
=== WITE2 中文化 v4 (内存改写) ===
词典: 65 条
[patch] .rdata: 改写 281 处
改写 281 处, 译文过长跳过 0 条, 未找到 0 条
[hook] 兜底 hook 已安装
```

- **改写 281 处** —— 65 条译文在游戏里被多处复用
- **未找到 N 条** —— 该字符串不在 `.rdata`，可能是运行时拼接的
- **译文过长跳过 N 条** —— 中文比英文长，需要缩短译文

---

## 法律声明

本仓库**不包含任何游戏文件**。

- 不包含游戏 exe
- 不包含游戏数据文件
- 不包含从游戏提取的文本表

只包含补丁代码、字体子集化脚本，以及我自己写的翻译词条。

使用本补丁需要你**合法拥有**《Gary Grigsby's War in the East 2》。
仅供个人使用，请勿分发修改后的游戏文件。

游戏版权归 2by3 Games / Slitherine 所有。
