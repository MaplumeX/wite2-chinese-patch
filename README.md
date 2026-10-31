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
| 翻译进度 | 界面 5909 条 + 剧本描述 23 个文件 |

**技术框架已完整跑通。** 界面文本从 exe 的 `.rdata` / `.data` 完整挖出，
玩家实际能看到的界面文本基本都翻了；剩余主要是编辑器专用指令和内部调试串。

**部队番号与地名不做翻译。** `Dat/GenData/*.dat` 里的部队名、将领名、编制类型
（`113th ID`、`Army Group Center`、`Pz Div` …）一律原样显示英文。
`Locations.csv` / `MapText.csv` / `Nations.csv` / `Leaders.csv` 里的城市、机场、
国家、将领名字也一样原样显示（`Berlin`、`Bryansk`、`Poland`、`Soviet Union`）。
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
| **`Dat/Save/translation.csv`（官方翻译表）** | **无效，已实测排除，不要再试** |

#### 关于 `translation.csv`（已排除）

exe 里确实有完整的机制：

```
0x9BB044  'translation.english.csv'      ← 这份文件在游戏目录里不存在
0x9BB354  'translation.csv'              ← 场景文本紧邻主菜单 UI 字符串
0x90DF18  'ID,English Key,English Length, Description, max length, Translate?, Translated Text'
```

`Dat/Save/translation.csv` 也已存在（65 行，ID 5535…6623，全部 `Translate?=Y`），
但它**不起作用**。不要去折腾它，不要把它当翻译通道，也不要再花时间
验证怎么让它生效 —— 已经试过了。

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

1. **部队番号、地名与编制类型不翻译**
   部队名、将领名、编制类型（`113th ID`、`Army Group Center`、`Pz Div` …）
   一律显示英文原文。存档、导出 CSV 也是英文。
   同理，`Locations.csv` / `MapText.csv` / `Nations.csv` 里的城市、机场、国家名
   （`Berlin`、`Abingdon`、`Poland` …）不翻译。这类串既是地图显示文本，也是
   数据层按名查找的键，翻成中文后查找不到记录就会崩溃。
   早期版本曾整批翻译过约 1360 条地名/国名/人名，已全部移除。

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

## 静态分析：用交叉引用判断"这条串能不能翻"

`check_danger.py` 那套正则只能看"长得像不像标识符"。真正的判据在二进制里：

> **一条字符串如果被代码或指针表引用，它就是数据（键名/枚举/存档字段）；
> 哪儿都没被引用，它只可能是 UI 层拿来画的字。**

### 先解决一个坑：`.text` 在磁盘上是密文

`.text` 段磁盘熵 8.000，Ghidra 直接打开只能看到垃圾指令。但运行时内存里
是明文，`tools/dump_code.c` 已经 dump 过一份（`wite2_text.bin`，长度
`0x50A983` 与 `.text` 的 `VirtualSize` 完全相等）。

```bash
python tools/make_decrypted_exe.py      # 把明文 .text 写回 PE
python tools/make_decrypted_exe.py --check   # 只校验长度对不对
```

产物 `build/WarInTheEast2.decrypted.exe` 段表、重定位、绑定导入都不动，
Ghidra 能正常建函数和交叉引用。**只能拿来分析，不能运行**（游戏自己的
解密步骤会把明文再解一次）。

### 地址体系：README 里的地址是 RVA

这个坑踩过一次，记一下：

| 记号 | 值 | 说明 |
|---|---|---|
| README / `hook_text.c` 里的地址 | `0x45CE60` | **RVA** |
| PE 里的 preferred base | `0x400000` | 静态 VA = base + RVA |
| Ghidra 里的地址 | `0x85CE60` | `0x400000 + 0x45CE60` |
| 运行时日志 `[dt] hooked at` | `0x00C5CE60` | 实际基址是 `0x800000`（ASLR） |

三者能对上：`0x85CE60` 处字节是 `83 EC 38 56 8B F1`，正是绘制函数的序言，
`0x85CF20` 是 `mov ebx, [esp+0x50]`。**查 Ghidra 时地址要加 `0x400000`。**

### 引用风险核查

```bash
python tools/refcheck.py --csv build/risk.csv    # 秒级，不需要 Ghidra
python tools/refcheck.py --risky                 # 只看高危
```

判据分三档，从硬到软：

| 级别 | 证据 |
|---|---|
| `critical` | `.text` 里出现它的地址，且前面是 `68`/`B8+`/`C7 05` 这类真的当地址用的 opcode |
| `high` | `.data` 里有 dword 指向它（全局表持有 → 键名/枚举/存档字段） |
| `medium` | `.rdata` 里有 dword 指向它（只读表持有，多数是显示用） |
| `low` | 没有任何引用 → 纯显示文本 |
| `absent` | `.rdata` 里根本没有，说明只在 `.data` 段或数据文件里 |

`critical` 一定要排除 `E8`/`E9`（`call`/`jmp` 的 rel32）：5 字节 call 的后 4 字节
经常恰好拼出一个落在 `.rdata` 里的数，不排除会报 16 条全假阳性。

当前词表 5912 条的分布：

```
medium      39     .rdata 指针指向
low       4979     零引用 —— UI 菜单/按钮/提示，放心翻
absent     892     .rdata 里没有这条（.data 段或数据文件）
high         2
critical     0
```

那 39 条 medium 里混着 `Save version`、`map hex data`、`hex control`、
`SET TO`、`ADD`、`SUBTRACT` 这类**存档段名和编辑器公式操作符**，
是真正要逐条复核的部分——它们被 `.data` 的指针表持有。

> 误报率量级：`.data` 只有 356 KB，而目标区间 `[0x90C000, 0xA25000)` 占
> 4 GB 地址空间的 0.026%，约 23 个随机 dword 会"碰巧"落进去。所以
> `high`/`medium` 应当作**人工复核清单**，不是自动删除依据；`low`
> （零引用）才是可以放心批量翻的部分。

### 一个反直觉的结论

菜单文本（`Game Options`、`Load Saved Game`、`HOT KEYS` …）在 `.text` 里
**一处立即数引用都没有**，`.rdata`/`.data` 里也没有指针指向它们。
扫描 `.text` 得到的 9402 个指向 `.rdata` 的立即数里，一个都不落在这些串上。

这解释了为什么"扫描堆 + 原地改写"会崩：游戏不是靠 `strcmp(字面量)` 找这些串的，
而是启动时按某种池化/映射方式把它们装进运行时结构。所以只能改**磁盘上的
源文件**（启动前），不能在运行时改堆上的副本。

### `.dat` 是不是自研加密

用 Ghidra 复核了加载链（地址是 RVA）：

```
0x1BA400  dat 对象 Load()          memset(_Dst,0,0x404) → fopen → fread(0x404)
0x1BB750  .dat 加载分发器
0x45F5F0  fopen 转发 → 0x85F280
0x45EB80  fread（内含 zlib gzread 风格的缓冲/inflate 分支）
```

`.dat` 文件头 10 字节在所有文件里完全相同，第 11 字节起才分叉：

```
w_unit.dat    10 95 ff f9 e0 33 b4 f1 9e 79 ed b3 ae 71 df aa
w_ground.dat  10 95 ff f9 e0 33 b4 f1 9e 79 ed 93 ae 6d de ba
```

这正是"固定密钥流 + 相同明文前缀"的特征（明文头 `_2by3 WITE2_` 前 10 字节
所有文件都一样）。熵 7.912，zlib/gzip/raw-deflate 从偏移 0..79 逐个试都解不开。
**离线解密仍然不通**，README 里"部队名不翻"的取舍继续成立。

### 一个必须挡住的坑：存档段名表

`.rdata` 里 `0x9A41B0` 起有一张 **65 项指针表**，指向存档文件的数据块名：

```
save version / map hex data / sevices / aircraft / ground elements /
toe(ob) / leaders / ground types / nations / locations / units /
air groups / planes / pilots / flights / pools (ground) / pools (air) /
hex control / battles data / factory data / game events / loadmap / withdraw
```

证据链：

| 观察 | 方法 |
|---|---|
| 65 项里有 65 项带 `reloc type=3`（HIGHLOW）→ **真的是指针表**，加载时会被修正 | 解析 `.reloc` 块（注意 `TypeOffset` 低 12 位才是页内偏移）|
| `.text`/`.rdata`/`.data` 里**没有任何地方**存着 `0x9A41B0` 这个地址 | 全 102 MB 内存字节扫描 + Ghidra `xrefs`，两种方法结果一致 |
| 存档 `.sav` 前 11 字节 `10 95 FF F9 E0 33 B4 F1 9E 79 ED` 与 `GenData/*.dat` 完全相同 | 直接读文件头 |

**静态不可达，但 `reloc` 存在 —— 所以不能断定运行时不用。**

这张表曾把玩家存档搞坏过一次。原来的词典收录了其中 57 条，实际改写了 44 条：

```
save version  → 存档版本        hex control   → 六角格控制
map hex data  → 地图六角格数据    factory data  → 工厂数据
loadmap       → 载入地图         button states → 按钮状态
```

为什么必须排除：

- **零收益** —— 这些是存档内部标签，玩家界面上永远看不到。
- **风险明确** —— 如果它们参与存档读写，新档自洽（写读同一张表），
  但**所有旧存档全部失效**，多人对战交换存档也会出问题。
- 和地形枚举（`WATER`/`RIVER`）、配置键名（`SoundFx`）同类，
  归入“数据层，绝不翻译”。

```bash
python tools/save_sections.py           # 列表 + 对比词典
python tools/save_sections.py --fix     # 从 zh_dict.json 删除
```

工具是**自动定位**的（扫 `.rdata` 找“连续 ≥20 项指向小写标识符串”的最长指针表），
不硬编码地址，游戏更新后仍能跑。

> 坐标陷阱：表里存的是 **VA**（`0x9A4824`），段表给的是 **RVA**。
> 转文件偏移必须 `file_offset + (va - image_base - rva)`，
> 写成 `file_offset + (va - rva)` 会多算一个 `image_base`，扫出来空表。

### 三个真实 bug（这轮逆向才暴露的）

#### 1. 格式串剪枝把**所有单数字**都杀了

`dt_check_body` 的格式串循环前面有这么一行：

```c
if (e->klen > len) continue;   /* “模板至少比实例长” */
```

这个假设是错的。`%d` 占 2 个字符，但最小只输出 **1 位数字**：

```
key  "%d turns"   klen = 8
text "3 turns"    len  = 7      ← 8 > 7，被剪掉
```

后果是 `Turn 5`、`3 HQs`、`3 turns` 这类**从来没被翻译过**，而 `12 turns`
正常 —— 一个很难注意到的“有时好有时不好”。

修法：按占位符的**最小宽度**算（`fmt_minlen()`），`%d` 计 1、`%s` 计 0、
`%20s` 计 20、`%%` 计 1。`tools/test_match.py` 里加了 4 条回归用例。

#### 2. 词典里 8 条 `#` 开头的条目被当成注释丢掉

TSV 的第一行就是 `# WITE2 中英对照表…`，加载器用 `line[0]=='#'`
跳过注释。于是 `### ERROR : game failed to save ###`、`# of Engines:`
这些**真条目**被静默丢弃，从来没生效过。

#### 3. 含制表符的条目会把 TSV 列结构搞坏

选项说明那几段长文本（两栖登陆规则 / 将领死亡 / 战斗迟滞 / 战役结束条件）
用制表符做缩进对齐。TSV 恰好用 TAB 当分隔符 —— 一条带 TAB 的 key
会把整行解析偏位。

**统一解法：`@` 转义行。** 以 `@` 开头，后跟转义过的 key + 真 TAB + 转义过的译文：

```
@### ERROR : game failed to save ###	### 错误：游戏保存失败 ###
@# of Engines:	发动机数量：
@STANDARD - ...~\t\t\t~LIMITED - ...	标准 - ...~\t\t\t~有限 - ...
```

转义规则：`\t` = TAB，`\n` = LF，`\\` = 反斜杠。key 和译文都要转义。
C 侧在 `dict_load` 里处理，`tools/verify_tsv_load.py` 是对应的验证器。

> 顺带修掉了 `build_assets.py` 里 `str(v).startswith("----")` 的过滤：
> 它会把「---- 轴心 ----」这种**真译文**一起滤掉。真正的占位标记是
> 恰好 4 个短横，而那些本来就是 `_` 开头的分区标题。

### 字符串清单：不要手写扫描器

`.rdata` 里 ASCII 串和 UTF-16 串**混排**，ASCII 的单字节 `\0` 会改变
UTF-16 的对齐。手写的字节扫描器会产出这种假串：

```
'South Paris' + '\0' + 'Create Depot Here ?'
   → 错位读成 'sCreate Depot Here ?'
```

所以现在用两路合并（`tools/string_inventory.py`）：

| 来源 | 条数 | 盲区 |
|---|---|---|
| Ghidra `list_strings` | 17864 | 只列**已定义**的 string Data，`'Export to CSV?'` 这种未识别的不在内 |
| 严格自扫描 | 18570 | 只接受前两字节为 `00 00` 的起点，会漏紧跟 ASCII 串的 UTF-16 |
| **合并去重** | **12782** | — |

Ghidra 那份导出要持久化，否则每次都要重跑分析（MCP 直连会超 10 MB 响应预算）：

```bash
rea search build/WarInTheEast2.decrypted.exe \
    --kind strings --mode regex --pattern "." --provider ghidra --format json \
    > build/rea/allstrings.json
```

### 覆盖率与缺口

```bash
python tools/coverage_report.py --gaps build/rea/gaps.csv   # 权威口径
python tools/coverage_report.py --show 20                   # 看缺口样例
```

以权威清单 12782 条为分母：

```
覆盖   5360  (41.9%)      缺口   6787  (53.1%)      数字/布局  635
```

缺口按用途分桶，**只有前两类值得动手**：

| 桶 | 条数 | 要不要翻 |
|---|---|---|
| `dialog` | **0** | ✅ 已全部补完（75 条）|
| `other` | 3644 | 大部分是部队番号（`JG`/`SG`/`Corps`/`OB West`）和 HTTP 令牌，按约定不翻 |
| `fmt` | 1327 | 多是引擎日志（`Screen size %d x %d`、`GPU %s`、`engine.log`）|
| `path` | 541 | 资源路径 |
| `editor_input` | 490 | 编辑器输入提示 |
| `ident` / `enum` | 599 | 键名、枚举 |
| `log` / `editor_check` | 186 | 日志 |

### 补完的那 75 条对话框

`tools/add_dialogs.py`，玩家可见的多行提示：

```
Build Fortified Region?~Current Units - %d  Limit - %d ~Cost - %d Admin Points
Ends at Start of Turn %d   OR   if SOVIET Player Captures All VP Objectives
Select this leader~ ~%s~%s %s~ ~Political:  %d~Morale:   %d~Initiative:   %d
Click to set withdraw.~Air group will withdraw: ~%d %.3s %d (if %s moves first)
Motorize Unit? ~ ~ Cost - %d AP, %d trucks
Set loadout to all~%s in~%s air directive?
```

约束（任何一条不满足就不加）：

1. `~` 数量必须一致 —— 它是游戏内的换行符
2. `%d` `%s` `%u` `%%` 的**数量和顺序**必须一致（`fmt_match` 按位置回填）
3. `____________` 下划线是列对齐填充，原样保留
4. `<img %d>` / `<scl 50>` 是标记语言，不能动

带制表符的大段不手写：按 `~` 切段，**只替换文字、制表符原样拼回**，
避免数错缩进。

### 被埋很久的一批：游戏内可见消息

`other` 桶一直是垃圾桶，把四类完全不同的东西倒在一起：

| 类 | 条数 | 要不要翻 |
|---|---|---|
| `token` | 4070 | 符号碎片、格式常量（`0x` `th` `st` `rb` `PM`）|
| `misc` | 1497 | 库内部碎片（`="` `=;` `>>`）|
| `unit` | 694 | 部队番号 |
| **`ui_msg`** | **618** | ← 这里藏着真东西 |
| `person` | 249 | 将领姓名 |
| `lib` | 244 | zlib / WinHTTP / CRT errno |
| `editor_tag` | 50 | `<add unit>` `<Set Date>` |

`ui_msg` 再拆：剔除引擎日志后 520 条，其中编辑器输入提示 365 条，
剩 **155 条像是游戏内可见**；人工过一遍，剩 **72 条**玩家真能看到。

这 72 条共同的特征：**全是 `.rdata` 里的 ASCII 字符串，不是 UTF-16**。
之前所有扫描器只扫 UTF-16，所以它们从来没进过候选清单：

```
.rdata 里  UTF-16 串 9947 条   ASCII 串 2826 条
```

```bash
python tools/triage_other.py --csv build/rea/other.csv   # 自动分桶
python tools/curate_ingame.py                            # 人工策展结果
python tools/add_ingame_msgs.py                          # 写入词典
```

典型条目：

```
Unable to contact server! / Can't play this game! / Wrong Turn Not Player's Turn
This order requires %d admin points! / Can't build more than %d fort units
Click to set delay.~Unit will arrive: ~%d %.3s %d
Set supply priority to %d for the selected units?
Failed to save temporary file / Ground data - failed intergrity check
```

### 消息框要单独钩

这 72 条里有一部分**不走绘制层**：

```
导入表:  MessageBoxA  YES (IAT 0x90C350)      MessageBoxW  NO
绘制钩:  只钩 0x85CE60（参数 wchar_t*）
```

`MessageBoxA` 收 **ANSI** 字符串，和 `wchar_t*` 是两条路，加再多词条也
不会生效。所以另开一个导入表钩子：

```
MessageBoxA → CP_ACP → 查词 → UTF-16 → 改用 MessageBoxW 弹
MessageBoxW → 直接过 dt_check_body（和绘制层同一套匹配）
```

#### 两个踩过的坑

**1. `MessageBoxW` 没被导入。** 这个 exe 的 USER32 静态导入表有 41 个
函数，包括 `MessageBoxA`，但**没有 `MessageBoxW`**。直接用
`g_origMsgBoxW` 永远是 NULL，中文发不出去，只能退化成原文。所以改成
惰性解析：

```c
static pMessageBoxW resolve_msgbox_w(void)
{
    HMODULE u = GetModuleHandleA("user32.dll");
    if (!u) u = LoadLibraryA("user32.dll");
    return (pMessageBoxW)GetProcAddress(u, "MessageBoxW");
}
```

user32 已因其他导入项加载，不增加新依赖。

**2. A 版是 ANSI 不是 UTF-8。** 一开始用 `CP_UTF8` 解码 `MessageBoxA`
的 `text`。报错文案都是纯 ASCII，两者恰好一样所以没暴露；但带重音字符
的文本（地名、将领名）会解码失败。改成 `CP_ACP`。

日志会写：

```
[msgbox] MessageBoxA=hooked MessageBoxW=not-imported (W 走 GetProcAddress 惰性解析)
```

关掉的方式：游戏目录放 `wite2_nomsgbox.ini`。

### 被排除规则误杀的界面标签

玩家报告：“部队详情里支援单位那列，`Artillery` 是英文，但防空那条
（`Anti-Aircraft`）是中文”。`Artillery` 在 `.rdata` 0x932B10，就在那张
编制类型表里，但从来没进过待翻清单。

两个原因：

**1. `inventory.py` 把它当“配置键名”挡掉了**

```python
BLOCK_EXACT = { …, "Art", "Bmr", "Ftr", "KILL", "LOSS", "DAMD", … }
BLOCK_PATTERNS = [(r"^[A-Z]{2,}\d*$", "全大写代号"), …]
```

但那些是 **`.data` 里的配置键名**；`.rdata` 里的同名串是**显示标签**，
两回事。同一短语可能两边各一份，改错一边就坏。

**2. `short_class()` 只看 ≤6 字符**

```python
return len(k) <= 6      # 超过 6 就默认走原地改写
```

`Art`(3) `AA`(2) `Mrl`(3) 会自动走绘制层，但 `Artillery`(9)
`Air Base`(8) `SP Artillery`(12) 不会 —— 它们会被原地改写。

### 为什么这批必须走绘制层

实测字节出现次数（要求前后 NUL）：

```
'Artillery'   .rdata 6 处 UTF-16 + 7 处 ASCII    .data 2 处 ASCII
'Air Base'    .rdata 11 处 UTF-16 + 28 处 ASCII
'AA'          .rdata 1 处
'Mrl'         .rdata 4 处
'AC'          .rdata 3 处 + 1 处 ASCII
```

`patch_section` 是**按段全扫精确匹配**，会改掉该段里**所有**副本。
多副本说明可能有枚举/查找用途，改了是静默破坏。所以这批全部走
绘制层（TSV 第三列 `t`）：只在那一次绘制调用里换指针，
**不动任何内存字节**。

安全性核查（`build/rea/probe_safety.py`）：

```
文本             副本   代码   rd指针  data指针  判定
'Artillery'     13    0     3      2       ⚠ 只绘图
'AA'             1    0     1      0       ✅
'Mrl'            4    0     0      0       ⚠ 只绘图
'AC'             7    0     0      0       ⚠ 只绘图
```

**“代码”列全 0** —— 没有任何一处被 `.text` 立即数直接引用，
所以不存在 `strcmp` 依赖（对比：`WATER`/`RIVER` 那些有）。

### 补了两批，共 77 条

```bash
python tools/add_unit_labels.py      # 第一批 46 条
python tools/add_unit_labels2.py     # 第二批 31 条
```

覆盖的完整表结构（`.rdata` 连续布局，UTF-16）：

| 地址 | 内容 |
|---|---|
| `0x9328FC-0x93297C` | 编制规模 `Brigade/Division/Front/Company/Battalion/Regiment` |
| `0x93297C-0x932AA0` | 兵种缩写 `Mech/Cav/Airb/Mot/Crps/SPGun/MtInf/Rckt/Mort/…` |
| `0x932AAC-0x932C98` | 兵种全称 `Infantry/Motorized/Mechanized/Armored/…` |
| `0x932D88-0x932FD0` | 编制操作 `Refit/Set MAX TOE percent/HQ Chain/…` |
| `0x932144-0x932178` | 将领属性 `Def/Defeats/Vic/Victories/HQ Types` |
| `0x92D0C8-0x92D154` | 飞行队统计 `SKILL/LOSS/KILL/DAMD/…` |
| `0x92E790-0x9326D8` | 指挥官报告列头 `Bmr/Ftr/Mrl/AC/Inf/Sup/Man/Veh/…` |

### 缩写必须跟全称一致

补的过程中抓到 2 处不一致：

```
'Sec'    → 治安     但 'Security'    → 警备     ← 同一概念两种译法
'AirLnd' → 空降着陆  但 'Air Landing' → 空降
```

已全部对齐（`Sec`→警备、`AirLnd`→空降），`add_unit_labels2.py` 里
加了一致性检查表，以后加新缩写会报冲突而不静默采用。

### `.data` 里还有一套独立的标签表

玩家报告“部队详情里仍有 `Machinegun` 英文”。全 exe 搜索发现：

```
'machinegun'  1 处  ->  .data 文件偏移 0x64F4C4  (VA 0xA506C4)
'Machine Gun' 6 处  ->  .rdata  UTF-16 2 处 + ASCII 4 处
```

`.rdata` 里只有带空格的 `Machine Gun`，**无空格的 `Machinegun` 只存在于
`.data`**。玩家看到的是无空格那个，说明部队详情这一屏走的是 `.data` 表。

前面所有扫描器只扫 `.rdata`，所以整张 `.data` 表都没被覆盖。补扫后发现
`.data` 里有三张平行的兵种标签表（VA，ASCII，步长 `0x14`）：

| 地址 | 内容 |
|---|---|
| `0xA503A4-0xA50444` | Infantry / Artillery / SP Weapon / Mech. Infantry / … |
| `0xA50520-0xA50714` | none / Armor / … / **Machinegun** / **Air base** / Naval |
| `0xA50840-0xA508B8` | none / Combat / HQ / Support / Partisan / Multi-Role / City Fort |

两套表**拼写不一样**，这本身就是证据——它们是独立维护的：

```
.rdata  'Machine Gun'        .data  'Machinegun'      ← 少个空格
.rdata  'Air Base'           .data  'Air base'       ← b 小写
.rdata  'Corps HQ'           .data  'Corp HQ'        ← 少个 s
.rdata  'Motorized'          .data  'Motorized Infantry'
```

而且**大小写不敏感命中的词不用补**：`Armor`/`Naval`/`Air base`/
`Multi-Role`/`City Fort` 词典里已有 `armor`/`NAVAL`/`Air Base`/…，
绘制层查词忽略大小写，能命中。真正缺的只有 3 条。

### 绘制层的“断章取义”风险

这是个真问题，而且比预想的严重。绘制层是按**整串精确匹配**查词：

* 好处：不会像子串替换那样误伤（`Turn` 不会把 `Return` 改掉）。
* 坏处：**同一个英文串只能有一个译文**，不管它出现在哪个界面。

#### 实例：两个差点弄错的陷阱

这两个是我准备加进词典时拦下来的：

```
'Major'   0x9A28C4   邻居 'Minor'(次要) / 'Impass' / 'River Art Idx'
                     -> 地图文字编辑器的「主要河流」，不是军衔
'Private' 0x9DF3B4   邻居 'StepMaxTime' / 'ChallengePassword'
                     -> PBEM 存档的 JSON 字段名，不是军衔
```

如果按“英语词典直觉”去翻，会得到「少校河流」和「列兵存档」。
已记在 `zh_dict.json` 的 `_ambiguous` 元数据里，附地址和原因。

#### 实例：候选提取把非界面表也扫进来了

```
'Range' @ 0x913C8C   邻居 'Proxy-Authorization' / 'Referer' / 'Retry-After'
                     -> 这是 HTTP 请求头表，被当成 UI 词汇译成了「射程」
```

HTTP 头永远不会被绘制，所以这条当前无害；但它说明**候选来源必须
按区段鉴别**，不能只看“是不是可读 ASCII”。

#### 实例：大小写变体把同一概念拆成两个译法

游戏在网格/表头里用全大写（`PRIORITY`），在普通标签里用首字母大写
（`Priority`）。词典把它们当独立键，分批补词就漂了。实测 **15 组**：

```
Airbase  航空基地  vs  AirBase/Air Base  空军基地     ← 漂移
Priority 优先     vs  PRIORITY/Pri      优先级      ← 漂移
NORMAL   普通     vs  Normal            正常        ← 漂移
AIR LOSSES 空战损失 vs Air Losses        空中损失     ← 漂移
flak     高炮     vs  Flak              高射炮      ← 漂移
on the ground 地面 vs On The Ground     在地面      ← 漂移（「地面」已被 Ground 占用）
POOL     储备     vs  Pool              储备池      ← 漂移
---
RANGE 航程 / Range 射程          飞机航程 vs 火炮射程    ← 语义确实不同，保留
MP 移动力 / mP 极地海洋性         移动点 vs 气候代码     ← 同形不同义，保留
RD. 步兵师 / Rd. 道路             番号 vs 道路         ← 保留
MEDIUM 中 / Medium 中型           档位 vs 尺寸等级      ← 保留
ACTIVE 激活 / Active 活动         按钮态 vs 事件条件     ← 保留
BLUE 蓝 / RED 红 / white 白       短译供窄网格          ← 宽度受限，保留
```

前 7 组已由 `tools/fix_case_drift.py` 统一，后 6 组连同理由记在
`_caseDriftKept` 里，防止以后被“好心”统一掉。

#### 为什么暂时不加入上下文参数

钩子**能**拿到上下文——它已经为了区域采集算出了当前控件：

```c
ui   = exe + 0x0E72800
idx  = [ui + 0x200]                 // 当前控件索引
arr  = [ui + 0x218]                 // 控件数组，元素 0x30
ctl  = [arr + idx*0x30 + 0x20]      // 控件本体，[ctl] 是 vtable
```

所以技术上可以给词条加“只在某个控件 vtable / 索引下生效”的限定。
但**现在不做**，理由：

1. 没有确认的冲突实例。上面两个陷阱已经靠“不加”解决了。
2. 现有 `wite2_regions.tsv` 是旧版本抓的，`ctl_vt` 列全 `0`，
   无法用它判定哪些词条真的需要分上下文。
3. 改动会碰全部 6104 条共用的匹配路径，回归风险大于收益。

触发条件：一旦在实测中确认某串在两个界面必须不同译法，就加一个
**独立的高优先级覆盖表**（按 `(text, ui_idx)` 或 `(text, ctl_vt)` 命
中，命中不了就落到原全局词典），不碰现有路径。

---

## 调试

`wite2_hook.txt` 记录运行情况：

```
=== WITE2 中文化 ===
词典: 5900 条
[patch] .rdata: 改写 281 处
改写 281 处, 译文过长跳过 0 条, 未找到 3 条
[zh] 5900 entries ready (173 format templates)
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
