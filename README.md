# War in the East 2 中文化补丁

给《Gary Grigsby's War in the East 2》加中文显示的注入式补丁。

**不改游戏 exe**，只放一个代理 DLL 到游戏目录，随时可删除还原。

---

## 当前状态

| 项目 | 状态 |
|---|---|
| 中文字形显示 | ✅ 可用 |
| 界面文本（主菜单/按钮/提示） | ✅ 可用 |
| 剧本历史描述 | ✅ 可用 |
| 部队番号 / 编制类型 | ❌ 不翻译（保持英文原文） |
| 抗游戏更新 | ✅ 只需补翻新增条目 |
| 翻译进度 | 界面 7243 条 + 剧本描述 23 个文件 |

**技术框架已完整跑通。** 界面文本从 exe 的 `.rdata` / `.data` 完整挖出，
玩家实际能看到的界面文本基本都翻了；剩余主要是编辑器专用指令和内部调试串。

**部队番号不做翻译。** `Dat/GenData/*.dat` 里的部队名、将领名、编制类型
（`113th ID`、`Army Group Center`、`Pz Div` …）一律原样显示英文。
理由见下方“为什么部队番号不翻”。

### 词边界匹配

早期版本有过一个典型的子串误伤 bug：词典里有 `Start` → `开始`，
绘制 `Starting player` 时在位置 0 命中 `Start`，替换后剩下 `ing player`，
最终显示 `开始ing player`。

修法：**只用整串匹配，不做子串替换**。

1. **整串精确匹配** —— key 必须和绘制文本完全相等（长度 + 内容），
   从根上就没法出现 `开始ing player` 这种事
2. **最长优先** —— 词典按 key 长度降序排序，`Show Production screen`
   先于 `Show` 命中

同类问题还有 `National` → `国家al`、`Ending` → `结束ing`、`Mapping` → `地图ping`，
都被同一条规则解决了。`tools/test_match.py` 里有对应的回归用例。

---

## 原理

### 难点一：字形 —— 必须字体子集化

游戏引擎用 GDI 把字光栅化进 D3D9 纹理图集：

```
CreateFontW → GetFontUnicodeRanges → 逐字符 ExtTextOutW → 上传纹理
```

直接用系统里装的中文字体会**卡死**：微软雅黑声明了 29905 个码位，
引擎会试图全部光栅化，47 个字体实例 × 上万字形 → 图集爆掉。

**解法：** 用 `fontTools` 只生成包含"实际用到的字"的字体，并把内部
family name 改成唯一名（`WITE2ZH`），避免引擎去加载系统里那份完整字体。

```
29905 码位 → 343 码位
19 MB      → 173 KB
```

```
FONT WITE2ZH size -19 : 256 glyphs, 1 pages (512x512), 512 KB atlas
```

### 难点二：exe 代码段在磁盘上是加密的

`.text` 段磁盘熵 8.000，静态分析找不到任何函数。运行时内存中解密（熵 6.77），
可以 dump 出来分析。

界面文本本身是 **UTF-16 明文**，躺在 `.rdata` 里，启动时原地覆盖即可：

```
"Game Options"                          26 字节 → "游戏选项"      10 字节
"Set to human controlled Axis player."  72 字节 → "设为玩家控制轴心" 18 字节
```

中文在 UTF-16 下远比英文短，原地覆盖空间充足。这条路径**运行时零开销**。

### 难点三：部队名不在 `.rdata` 里（所以本项目不翻它们）

部队番号、将领名、装备名存在 `Dat/GenData/*.dat`，是**自研流密码加密**的。

逆向出的加载链（基于运行时 dump 的 5.2MB 解密代码）：

```
0x371210 / 0x371280   遍历所有 .dat 的加载分发器
  └─ 0x1bb750         .dat 对象 Load()
       └─ 0x37fdc0    按文件名加载
            └─ 0x1ba400   真正读取（先读 0x404 字节）
                 ├─ 0x45f5f0   自定义 fopen
                 └─ 0x45eb80   自定义 fread ← 解密在这里
```

证据：读 0x404 字节后立即 `strcmp(buf, "_2by3 WITE2_")` 成功，说明 fread 返回时已是明文。

密码学分析确认这是**自研强流密码**：无 AES S-box、无 ChaCha 常量、密钥流不复用、
非块对齐。静态破解不可行。

#### 为什么干脆不翻部队番号

除了技术上难以静态修改，更实际的原因是**译名无法校对**：

- 部队显示名 = `Units.csv` 的 `Name` + `OBs.csv` 的 `Suffix` 在运行时拼成，
  组合数量远超 8000 条，逐条人工校验不现实。
- 一旦允许“拼装”，就会得到看起来很对、实际没人能核对的串：
  `II/42nd Mot. sFH Heavy Howitzer Battalion` → 「第2/第42摩托化…营」。
- 同一个部队在不同位置有缩写/截断形式（`113th ID`、`Army Grp. Sout`），
  要建一张缩写展开表才能匹配，维护成本高且容易误伤界面普通词。

所以本项目的取舍是：**界面文本全翻，部队番号与编制类型一律保留英文原文**。
宁可让玩家看到英文番号，也不给一个无法核对的译名。

### 难点四：五条失败路线（都是坑）

| 尝试 | 结果 |
|---|---|
| 改 `.dat` 解密后的 ASCII 缓冲 | 显示源是后来的 UTF-16 副本，改了没效果 |
| hook `MultiByteToWideChar` | 部队名不走它，日志里全是文件路径 |
| hook `0x1B3EB0`（逐字节扩宽） | 全程只 7 次调用，len 468~2MB，是整文件转换 |
| 扫描堆 + 原地改写 UTF-16 | **崩溃**：游戏用字符串做查找比较，改了导致 NULL |
| 改 `.rdata` 里的 ASCII 部队名表 | 2903 个名字，改了 53 处零效果 |

**关键教训：绝不能在数据层改字符串。** 游戏内部会拿这些字符串做查找、比较、
哈希，内容一变就查找失败。

### 绘制层换指针（界面文本的兜底通道）

`.rdata` 原地改写已经覆盖了大部分界面文本，但有三类串它碰不到：
数据文件里读出来的串、运行时拼出来的串、以及被标成“只改绘制层”
（TSV 第三列 `t`）的短串/枚举共存串。这些靠绘制层换指针解决。

绘制函数 `0x45CE60` 的第 4 个栈参数就是 `wchar_t*`：

```asm
0045cf20  mov  ebx, dword ptr [esp + 0x50]   ; ebx = wchar_t* 文本
0045cf3e  movzx eax, word ptr [ebx]          ; 读第一个宽字符
0045cf51  lea  edx, [edx + 2]                ; 逐宽字符遍历
```

序言 `83 EC 38 56 8B F1` 正好 6 字节，可完整替换：

```asm
pushfd / push eax / push ecx / push edx      ; 保存现场（16 字节）
mov  eax, [esp + 0x20]                       ; 取文本指针（原 [esp+0x10]）
push eax / call dt_check / add esp, 4
mov  [esp + 0x20], eax                       ; 换掉栈上的指针
pop edx / pop ecx / pop eax / popfd          ; 还原
jmp  [trampoline]                            ; 跳回原函数
```

**游戏数据一个字节都不改**，只在这一次绘制调用里换指针。零副作用。

### 匹配策略

**关键教训：绝不做自由拼接。**

早期版本用“任意位置子串替换”，只要短 key 出现在长串里就替换，结果：

```
"Show Production screen" → "Show 生产 screen"
"National"               → "国家al"
"Army Grp. Sout"         → "集团军 Grp. Sout"
```

加更多词条只会让碎片更严重。现在的策略只有五条，从严格到宽松：

| 顺序 | 策略 | 例子 | 触发条件 |
|---|---|---|---|
| 1 | 整串精确匹配（大小写不敏感） | `Show Production screen` → 显示生产界面 | 长度和内容完全相等 |
| 1.5 | 剥离尾部空格后精确匹配 | `1941 Campaign ` → 1941年战役 | 尾部有空格，译文补回等量空格 |
| 1.6 | 剥离扩展名后精确匹配 | `1941 Campaign.scn` → 1941年战役 | 尾部是 `.scn`/`.dat`/`.txt` 等 |
| 1.4 | 格式串模板匹配 | `Max TOE %` → 最大编制 % | key 含 `%d`/`%s` 等占位符 |
| 2 | 剥离尾部后缀后精确匹配 | 表格列头整串绘制 | 空格+`(`/`/`/`.` 或连续两空格处切开，**尾巴原样保留** |

策略 1.5 和 1.6 是补的。界面用空格填充对齐，绘制出的串常带尾部空格：

```
1941 Campaign      <- 词典里有，命中
1941 Campaign      <- 实际绘制的是这个（尾部 4 个空格），原来失配
1941 Campaign.scn  <- 剧本名从文件名派生，某些界面带扩展名，原来也失配
```

表现就是“有的剧本名翻了，有的没翻”——取决于该界面有没有做空格填充。
译文后面补回等量空格，保持原有对齐，不会让列表错位。

**部队番号不参与匹配。** `113th ID`、`XXIV Panzer Corps`、`Army Grp. Sout`、
`18th Panzer Divisio` 这类串没有对应词条，也没有拼装逻辑，直接原样返回。

`tools/test_match.py` 里有 57 个回归用例，覆盖以上全部策略和已知的失败模式
（包括“番号必须原样返回”的反向用例）。

### 剧本描述

`Dat/Scen/*.txt` 是**明文**的历史描述文件，游戏直接读取，不需要逆向。

```bash
python tools/scen_localize.py check    # 检查覆盖率
python tools/scen_localize.py apply    # 翻译并写入游戏目录
python tools/scen_localize.py restore  # 还原原始文件
```

23 个剧本共 656 非空行，逐行对应翻译，保持段落和换行结构不变。

注意：**字体必须包含剧本用到的汉字**。早期版本生成字体时只扫了 `zh_dict.json`，
漏掉剧本翻译的 284 个汉字，导致文字出现空洞、读起来语义不通。
`tools/build_assets.py` 现在会同时收集两处。

---

## 使用方法

### 1. 编译

```bash
build.bat
```

一条命令完成：编译 DLL → 生成翻译表和字体 → 跑回归测试 → 部署到游戏目录。

需要 Visual Studio 2019/2022 + C++ 桌面开发。游戏是 x86，必须 `-MACHINE:X86`。

只想做其中一步：

```bash
build.bat dll        # 只编译
build.bat assets     # 只生成翻译表和字体
build.bat check      # 只跑验证
build.bat deploy     # 只部署
```

### 2. 检查部署状态

```bash
python tools/deploy.py --check
```

会报告每个文件是否与构建产物一致、`GuiFonts.json` 有多少字体指向
`WITE2ZH`、剧本描述有多少已汉化。

### 3. 卸载

删除游戏目录下的 `version.dll`、`wite2_zh.tsv`、`Dat/Fonts/wite2_zh.ttf`，然后：

```bash
python tools/deploy.py --restore     # 还原 GuiFonts.json
python tools/scen_localize.py restore # 还原剧本描述
```

---

## 翻译校对流程

```bash
python tools/review.py export     # 生成 review.csv，Excel 直接打开
# 改"中文"列
python tools/review.py import     # 导回 zh_dict.json
python tools/review.py todo       # 生成待翻译清单 todo_translate.csv
python tools/review.py coverage   # 覆盖率报告 coverage.csv
python tools/review.py stats      # 统计
```

三个 CSV 都是 UTF-8 带 BOM，Excel 双击打开不乱码。

### 离线验证匹配逻辑

```bash
python tools/test_match.py          # 跑内置回归用例
```

用例喂给 `build/selftest.exe`（同一份 `hook_text.c` 编译出来的命令行
程序），跑的是**真实的 C 匹配器**，不是 Python 复刻——复刻会静静
漂移，曾经出现过“复刻全绿、DLL 出错”的情况。
改完词典先跑这个，能立刻发现词边界、最长匹配、尾部空格/扩展名的问题。

### 术语审校

```bash
python tools/term_review.py export   # 生成 term_review.csv
python tools/term_review.py import   # 导回修改
python tools/term_review.py check    # 检查词根译法一致性
```

把成体系的术语（补给后勤、空军、生产、游戏选项、战斗）
抽成一张表，按类别排序，方便一次过审。`check` 会列出同一英文词根
的不同译法，用来发现单复数不一致之类的问题（例如 `Air Group` 和
`Air Groups` 曾经分别译成「飞行队」和「航空队」）。

> 编制单位与兵种不再列入审校表——它们属于部队番号体系，本项目不翻。

### 找出遗漏的界面文本

```bash
python tools/inventory.py             # 完整清点 .rdata，按 22 类统计
python tools/inventory.py --todo      # 待翻译清单
python tools/inventory.py --blocked   # 核对排除项是否合理
python tools/vocab.py                 # 自举词表法待翻清单
python tools/triage.py                # 细分类器
python tools/scan_ui.py --stats       # 覆盖率
```

这几个工具解决的是同一个问题：**怎么知道还有多少界面文本没翻**。

`inventory.py` 的思路是"精确列出不能翻的，其余全翻"——比"猜哪些是界面文本"漏得少。
排除项分 22 类（地形枚举、配置键名、HTTP 头、MIME 类型、编辑器提示、剧本代号…），
每类都注明原因，`--blocked` 可以逐条核对。

`vocab.py` 用**自举词表**：从已确认的 5000+ 条译文里提取英文词汇，
再用它判断新串。这样能自动学会游戏的界面用语，而不是靠手写正则。
它迭代到收敛——每轮把新发现的串里的词加进词表，再扫一遍。

### 防止翻译破坏游戏

```bash
python tools/check_danger.py          # 列出危险词条
python tools/check_danger.py --fix    # 从词典删除
```

`.rdata` 里混着三类东西，只有第一类能改：

| 类型 | 例子 | 改了会怎样 |
|---|---|---|
| 显示文本 | `Game Options` | 正常翻译 |
| 配置键名 | `SoundFx`、`SfxGroupVolume` | **游戏读不到设置，声音消失** |
| 地形/资源枚举 | `WATER`、`RIVER` | **地图渲染出错，水体变黑** |

判定规则：

- **规则 B** — 驼峰标识符（`SoundFx`、`buildLimit`）或含下划线
- **规则 C** — 在游戏配置文件里作为键名出现，且符合规则 B
- **规则 D** — 全大写地形/资源枚举名

规则 B 只认驼峰和下划线，**不认首字母大写的普通单词**——`Supply`、`Moscow`
这些也是显示文本，配置文件注释里也会出现。

白名单 `SAFE_LABELS` 收录形式像标识符但确认是显示文本的条目
（`FrRec`、`NONE`、`AirGroups`），判断依据是 `.rdata` 里的邻居：
邻居是 `Depots`/`Range`/`MP` 说明是表格列标题，邻居是 `IMPASSABLE`/`//Tiles//`
说明是枚举。

### 两个段，不是只有一个

界面文本主要在 `.rdata`，但**天气和气候带在 `.data`**：

```
0x64E09E  Clear | Rain | Heavy Rain | Cold | Snowfall | Blizzard
0x64E160  Polar | Humid Cold | Humid | Humid Warm | Temperate Humid | Arid Desert
0x64F0B4  Polar Maritime | Tropical Maritime | Arctic Maritime
```

工厂/资源类型名也是（定长数组，步长 `0x28` = 20 个 wchar）：

```
0x630F20  NONE | GROUND | GROUND CHASSIS | AIRCRAFT | AIRFRAME
          HEAVY INDUSTRY | ARMAMENTS | VEHICLES | RESOURCES | OIL
          FUEL | SYNTHETIC FUEL | MANPOWER | PORT | RAILYARD
          V-WEAPON LAUNCH | V-WEAPON | U-BOAT | U-BOAT PEN
0x631228  同上短名版本（CHASSIS / HEAVY IND / V-W L / U-BPEN…）
```

只扫 `.rdata` 时无论加多少词典条目都翻不到这些。
`patch_all` 现在同时走 `.rdata` 和 `.data`，
`inventory.py --data` 可以单独清点该段。

### `.data` 的 VirtualSize 是陷阱

```
段名      VirtualSize   SizeOfRawData
.data     0x5AF1FAC     0x56E00
          ↑ 90 MB       ↑ 356 KB
```

`.data` 的 `VirtualSize` 把未初始化的 BSS 也算进去了，是 90 MB。
真实初始化数据只有 356 KB，全部落在 `SizeOfRawData` 内。

拿 `VirtualSize` 当扫描长度会让 5619 个词各扫 90 MB（合计约 500 GB），
启动直接卡死，日志停在 `.rdata` 那一行，`.data` 永远跑不到。
所以取两者较小值：

```c
DWORD span = sec->Misc.VirtualSize;
if (sec->SizeOfRawData && sec->SizeOfRawData < span)
    span = sec->SizeOfRawData;
```

另外 `.data` 只处理显式标记的词。否则 5000+ 个普通词会把 356 KB
的段各扫一遍，白花 2 GB 扫描量。

### 按段作用域（`_dataOnly`）

同一批工厂类型名在 `.rdata` 里另有**同名枚举副本**：

```
.data  0x630F20  ... | PORT | RAILYARD | OIL | FUEL | MANPOWER     ← 显示数组
.rdata 0x52B5C4  AIRFIELD | UNIT | RAILWAY | PORT | FERRY | INTERDICT | RAILYARD
                                                                   ← 任务类型枚举
```

`patch_section` 会把整段里所有精确匹配的串都换掉，所以只加一条 `PORT`
会同时改到枚举——就是 `WATER`/`RIVER` 那次地图变黑的翻版。

解法是给条目加作用域。`zh_dict.json` 里的 `_dataOnly` 列表：

```json
"_dataOnly": ["PORT", "RAILYARD", "OIL", "FUEL", "MANPOWER", "NONE", ...]
```

`build_assets.py` 把它写成 TSV 第三列：

```
PORT<TAB>港口<TAB>d
```

`d` = 只改 `.data`，`r` = 只改 `.rdata`，空 = 两者都改。

`dict_load` 必须先剥掉这一列再转成宽字符。漏了这一步，译文会带上
`	d`，界面上显示成「大雨d」——而且因为绘制层 hook 用的是同一份
词典，`.rdata` 改写和绘制替换会**同时**出现这个尾巴。

`build_assets.py` 写完 TSV 会回读校验（`verify_tsv`），模拟 C 的
`dict_load` 列解析，列数不对或译文含 tab 直接报错。这种错误写词典
时看不出来，只有跑游戏才暴露。
`check_danger.py` 认得这个标记，不会把已隔离的条目算成危险词条。

**`_dataOnly` 只放"仅在 `.data` 出现"的词。** 两段都有的词
（`PORT`、`NONE`、`OIL`、`FUEL`、`RAILYARD`、`MANPOWER`、`RESOURCE`、
`AIRCRAFT`、`GROUND`）必须继续改 `.rdata`——它们在 `fb79222` 版
就是这么翻的，实测正常。曾经把它们一起塞进 `_dataOnly`，结果
`.rdata` 少翻 197 处（`NONE` 一个词就 33 处），表现为"很多地方
的翻译突然丢了"。

`PORT` 为什么安全？`patch_section` 只替换整串（前后必须是 NUL），
路径里 `//Tiles//PORT//` 的 `PORT` 前面是 `/`，永远命中不了。
真正会出事的是 `WATER`、`RIVER` 这类作为独立字符串被代码当查找键
的地形枚举，改了地图渲染拿不到图形，水体变黑。

### 大小写敏感

地形词有大小写两种形式，用途完全不同：

```
生产界面:  ... | Port | Rail | Railyard     ← 显示标签，该翻
资源路径:  //Tiles//PORT//PortSymbol.tga    ← 枚举，不能翻
```

`.rdata` 改写用 `memcmp`，天然大小写敏感，只改 `Port`。
但绘制层是大小写不敏感，会误伤 `PORT`。

解法是给 `ZhEntry` 加 `cs` 字段，对地形词启用精确比较：

```c
if (g_zh[i].cs) {
    if (a != b) { ok = 0; break; }              // 精确
} else {
    if (lc_w(a) != lc_w(b)) { ok = 0; break; }  // 不敏感
}
```

### 收集游戏实际绘制的文本

游戏目录放一个空的 `wite2_collect.ini`，跑游戏，会生成 `wite2_strings.txt`。

收集的是**游戏真正画出来的字符串**，比从 CSV 猜准确得多——
CSV 里很多列存的是内部标识，而绘制文本才是玩家真实看到的东西。

### 按控件采集文本区域

`wite2_collect` 只知道"画了哪些串"，不知道"串挂在哪"。要精准翻译，
需要知道每个串对应的控件类型和位置。

游戏目录放一个空的 `wite2_regions.ini`（或直接开 `wite2_capture.ini`，
它会把区域采集一起打开），跑一遍各个界面，会生成 `wite2_regions.tsv`：

```
ret this this_vt ui_idx ui_cap txt_obj ctl ctl_vt x y style flags font text dst
```

`ctl_vt` 是控件对象的 vtable。寻址来自 `0x445020`（列表项绘制）：

```c
ui   = exe + 0xE72800      // 文件态 0x1272800，运行时重定位
idx  = [ui + 0x200]        // 当前控件索引
cap  = [ui + 0x21C]
arr  = [ui + 0x218]        // 控件数组，元素 0x30 字节
ctl  = [arr + idx*0x30 + 0x20]   // 控件本体，[ctl] 就是 vtable
```

为什么不用调用链：`0x4449A0` 是文本排版/自动换行的公共出口，
`0x445020` 是公共入口，3 帧栈链全部落在两者之间。实测 380 条捕获里
最粗的一条链独占 186 条，横跨主菜单、剧本列表、部队详情、地图浮窗、
编辑器。vtable 是精确地址匹配，假阳性几乎为零。

离线分析：

```bash
python tools/region_report.py                  # 自动找 wite2_regions.tsv
python tools/region_report.py --capture        # 回看旧 capture 数据
```

报告按控件 vtable、字体、槽位（同 vtable + 同 x,y）分组，并列出
**跨控件重复出现的文本** —— 这些就是同一个英文在不同位置需要不同
译法的候选（按钮要压到 2-4 字，表头 4-6 字，提示可以长）。

### 关于"译文过长"

绘制层是整体换指针，文字**不会被截断**。但界面布局按原文长度排版，
中文太长会视觉溢出或挤压相邻元素。

```
"Show Production screen"  22 字符 → "显示生产界面"     6 字   OK
"OKH"                      3 字符 → "国防军陆军总司令部" 9 字  过长
```

经验值：译文控制在原文的 **1.5 倍**以内。`review.py` 会自动标出超长条目。

### 改完必须重新生成字体

新增汉字如果不在字体子集里，游戏会显示空白或方块。`build.bat` 会自动重新生成。

---

## 为什么用 `version.dll`

游戏 exe 静态导入 `VERSION.dll` 的 3 个函数：

```
VerQueryValueA / GetFileVersionInfoA / GetFileVersionInfoSizeA
```

`version.dll` **不在** Windows KnownDLLs 列表里，所以应用目录优先。
系统 `version.dll` 只有 17 个导出，全部转发很干净。

（`d3d9.dll` 也可以，但要转发 30+ 个导出；`winmm.dll` 更多。）

---

## 抗更新

| 游戏更新时 | 结果 |
|---|---|
| exe 被 Steam 覆盖 | 无影响，补丁没改过它 |
| `version.dll` | Steam 清单里没这个文件，不会动 |
| `wite2_zh.tsv` | 独立文件，不会动 |
| 需要重做吗 | **不需要**，只需补翻新增文本 |

但有两个前提：

1. **绘制函数 RVA `0x45CE60` 可能变**。补丁启动时会校验序言签名
   `83 EC 38 56 8B F1`，不匹配就打印 `[dt] sig mismatch` 并跳过 hook——
   游戏照常运行，只是没汉化。这时需要重新定位。
2. **`GuiFonts.json` 可能被覆盖**，重跑 `build.bat deploy` 即可。

---

## 已知限制

1. **部队番号与编制类型不翻译**
   部队名、将领名、编制类型（`113th ID`、`Army Group Center`、`Pz Div` …）
   一律显示英文原文。存档、导出 CSV 也是英文。

2. **`.dat` 文件无法静态修改**
   自研流密码，已确认不可静态破解。

3. **`.data` 段只翻译了天气/气候**
   天气（`Rain`/`Snowfall`/`Blizzard`）和气候带（`Temperate Humid` 等）
   在 `.data` 段而非 `.rdata`，已纳入改写范围。该段还有约 150 条字符串，
   未逐条核查。

4. **`unitInfo.csv` 未处理**
   1168 段部队历史描述（含 `<cr>` `<i>` `<b>` `<url>` 标记）尚未翻译。
   这是纯数据文件，同样可以直接改，不需要逆向。

5. **需要 32 位编译器**
   游戏是 x86 程序，DLL 必须 `-MACHINE:X86`。

---

## 目录结构

```
src/hook_text.c          注入 DLL 源码（代理 + .rdata 改写 + 绘制层 hook）
src/version.def          17 个导出转发
tools/build_assets.py    生成翻译表 + 字体子集（统一入口）
tools/review.py          翻译校对工具（导出/导入/覆盖率/待译清单）
tools/test_match.py      离线验证匹配逻辑（不跑游戏）
tools/term_review.py     术语审校表（按类别导出/导入/一致性检查）
tools/find_gaps.py       找出翻译后仍残留英文的串
tools/inventory.py       完整清点 .rdata（22 类分类 + 排除核对）
tools/vocab.py           自举词表法找遗漏（迭代到收敛）
tools/triage.py          细分类器
tools/scan_ui.py         界面文本覆盖率
tools/check_danger.py    检测危险词条（配置键/枚举）
tools/scen_localize.py   剧本描述汉化（读写 Dat/Scen/*.txt）
tools/deploy.py          部署到游戏目录（--check / --restore）
tools/region_report.py   按绘制现场分区（控件 vtable / 字体 / 槽位）
tools/context_audit.py   绘制现场审计（同行邻居 + 槽位预算 + 状态分类）
tools/align_spaces.py    按像素宽度重算译文空格（修表格列错位）
tools/fit_check.py       译文/原文宽度比（找会溢出原位的条目）
tools/restore_game.py    从备份还原游戏 .dat 和 CSV
zh_dict.json             翻译源文件（权威数据）
scen_trans.py            剧本描述翻译表
wite2_zh.tsv             生成的翻译表（部署到游戏目录）
校对流程.md              校对流程详细说明
build.bat                构建脚本（编译/资产/验证/部署）
```

---

## 调试

`wite2_hook.txt` 记录运行情况：

```
=== WITE2 中文化 ===
词典: 7230 条
[patch] .rdata: 改写 281 处
改写 281 处, 译文过长跳过 0 条, 未找到 3 条
[zh] 7230 entries ready (173 format templates)
[dt] hooked at 00C5CE60 tramp=...
[dt-exact] "Show Production screen" -> 6 wchars
[dt-fmt] "Max TOE %" -> 4 wchars
[dt-suffix] "PRODUCED" + tail(18) -> 12 wchars
```

| 日志 | 含义 |
|---|---|
| `[dt-exact]` | 整串精确匹配命中 |
| `[dt-fmt]` | 格式串模板命中 |
| `[dt-suffix]` | 剥尾后前缀命中（尾巴原样保留） |
| `[dt] sig mismatch` | 绘制函数地址变了，需要重新定位 |

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
