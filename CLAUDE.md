# 开工入口 —— PC 工具与测试资产

**这份文件是给 AI 会话看的。**

这个仓库装两样东西：出货给客户的 **`IAPTool`**（Go，负责把固件烧进板子），以及**整套测试资产 `TestCase/`**（用例、主机侧单元测试、板上 sketch、自动化脚本、验收单）。

> 产品全貌：`<AI-Skills>/OpenPLC/docs/OVERVIEW.md`（本机位置见 `SKILLS_REPO`）。

## 这个仓库自己的东西

| 在哪 | 是什么 |
|---|---|
| 根目录的 `.go` | `IAPTool`：CDC / 以太网两条烧写通道、签名、认证、上传锁 |
| `cmd/porttool/` | `PortTool`：给硬件工程师的端口测试面板。**受众不同，所以是第二个 exe** —— 他的工具里不该有固件签名和 takeown |
| `internal/serialx/` | 两个 exe 共用的串口层：开口、重试、枚举（带 VID/PID）。⚠️ 详细枚举在 macOS 上要 cgo，那边按 build tag 退回只报端口名 —— 见 `enum_basic.go` |
| `internal/ptproto/` | `pt.*` 协议解析：四类行分流、`pt.caps`、采样帧、tick 回绕。**面板和 CLI 共用同一份，两边判据不会分叉** |
| `internal/ptboard/` | 一条串口连接的对话管理：**永不停止地读**（停读就丢帧，这条链没有重传）、命令逐条串行（协议没有请求 id）、环形缓冲 + 订阅 |
| `internal/ptpanel/` | 面板的 HTTP 面 + `go:embed` 的页面。推送用 **SSE 不是 WebSocket**（标准库，零依赖）。`Open` 字段是给测试注入假板子的缝 |
| `internal/ptcheck/` | **限值与读数比对的唯一去处**。面板和产线序列都调它，两边不可能对同一个数得出不同结论 —— 和 `ptproto` 管解析是同一条规矩 |
| `internal/ptplan/` | 方案文件（JSON）的读写与校验。**限值是步骤自己的参数，没有独立的限值表**（`$BOOT/docs/design/DECISIONS.md` 第 24 条）。`CheckAgainstCaps` 报只有板子能settle的事：端口不存在、参数固件不收、以及**给 `loop=ctrl` 端口写 `miss` 判据这种假判据** |
| `internal/ptseq/` | 执行器。**六个通用字段的语义住在这里**：`execute_condition` 的门、重试、前后延时、超时。⚠️ 门看的是「上一个真正跑过的步骤」，所以被跳过的步骤不会把前面的失败洗掉 |
| `internal/ptreport/` | 报告。三条规矩：**每次尝试都留**（重试不覆盖原失败）、**原始值都留**（限值会改，要能重判）、**超时与判定失败分开记**（前者多半是接线/探针，后者多半是板子） |
| `iapcrypto/` | 加密原语。**测试用例 import 它，不重写** |
| `TestCase/TEST-CASES.md` | **每个用例的判据、前置条件、怎么跑。判据贴着代码走，不搬去 docs/** |
| `TestCase/tools/` | 自动化脚本（烧写、抓串口、跑用例、各种一致性检查） |
| `TestCase/host/` | 不需要板子的检查：假板子、加密交叉验证、主机侧编译真实 bootloader C 源码 |
| `TestCase/onboard/` | 跑在板子上的验证 sketch |
| `TestCase/acceptance/checklist.md` | 出厂与发版验收单 |
| `TestCase/config/machine.py` | **本机路径的唯一出处**，gitignored，**生成的** |
| `TestCase/tools/init_machine.py` | 生成上面那份。**"这台机器有什么"的唯一记录是它里面的 `SETTINGS` 表**，没有模板可抄 |

## 开工前

```
cd TestCase
python tools/selfcheck.py          # 所有不需要板子的检查
python tools/selfcheck.py --list   # 先看它会跑哪几步、各证明哪条需求
```

**改完代码就该跑一遍** —— 上板调试一轮的成本高一个数量级。它的 **ENV** 一步会打出这台机器上每一项工具解析成什么，**缺什么点名说缺什么**，不静默跳过。

配置还没生成过（`config/machine.py is missing`），或者板卡包升级后路径变了，就跑 `python tools/init_machine.py`。那份配置是**生成的**，没有模板，来源写在它自己的文件头里。

## 脚本的平台规矩

**脚本一律 Python**，且要求**同时能在 Windows 和 Linux 上跑**。`tools/common.py` 是平台兼容层，写新脚本时：

| 要做的事 | 用这个 | 不要用 | 为什么 |
|---|---|---|---|
| 判断平台 | `PLATFORM` | 直接读 `os.name` | 三个平台三套叫法，集中在一处映射 |
| 拼可执行文件名 | `EXE`、`get_go_bin()`、`get_iap_tool()`、`get_programmer_cli()`、`get_cubeide_exe()` | 硬写 `.exe` | — |
| 临时文件 | `get_scratch_file()` / `get_scratch_dir()` | 自己拼 `$TEMP` | 平台之间那个变量不一致，拼错会往文件系统根目录写 |
| 相对路径 | `/` 分隔 | `\` | Windows 接受 `/`，Linux 不接受 `\` |
| Go 输出目录 | `GOOS_DIR` | 硬写 `windows` | — |
| 板卡包平台目录 | `A15_DIR`（`win`/`linux`/`macosx`） | 硬写 `win` | — |
| CubeIDE 插件后缀 | `CUBE_PLUG`（`win32`/`linux64`/`macos64`） | 硬写 `win32` | — |

**遇到一个新的、只有本机知道的路径** —— 加进 `tools/init_machine.py` 的 `SETTINGS` 表（连同探测方式和一段说明），不要硬编码，也不要猜。加进去它就会在每台机器上被自动找出来（理由见 `open_plc_cube_ide/CLAUDE.md` 第七节）。

> ⚠️ 双平台能力**只在 Windows 上验证过**。Linux 侧是逐条消除平台依赖做的，**没有真机验证**。

**机器相关的路径一律进 `$TOOL/TestCase/config/machine.py`，而那份是 `tools/init_machine.py` 生成的** —— 不手写，也没有模板可抄。

⚠️ **判断一个值该不该进配置：另一台同样系统的机器会不会有不同的值？** 不会就不属于那里 —— 那是平台派生量，归 `common.py`。

⚠️ **新的、只有本机知道的路径不许硬编码，也不许猜。** 该往哪儿加、为什么，在 `$TOOL/TestCase/tools/init_machine.py`。

> **2026-08-20 删掉了 `machine.example.py`。** 它们和 `SETTINGS` 表是同一份清单的两个出处。而且模板那套"两个平台的值都给、删掉不用的那套"的用法，**忘记删是最常见的错误** —— 第一次在 Debian 上就踩了，表现是一堆互不相关的 MISSING，加上一句让人去查串口线的错误建议。

⚠️ **这条不止管脚本内部，还管 AI 会话怎么打命令。** 从一个仓库的会话里调另一个仓库的脚本（例如在 `open_plc_cube_ide` 里跑 `IAPTranfer_Tool/TestCase/tools/selfcheck.py`），**用相对路径 `../IAPTranfer_Tool/TestCase`，不要绝对路径**。第三节那张兄弟目录表已经把布局钉死了，相对路径换机器天然成立；绝对路径（`E:\WorkSpace\...`）只在这台机器上对，写进 `.claude/settings.local.json` 的 allow 列表里，换机器就是一条永远不会再命中的死记录。

**2026-08-23 实测：allow 列表不支持在字符串中间用 `*` 匹配任意前缀**，只有结尾通配符是文档确认支持的（`Bash(git *)` 这种）。所以"允许这条命令、不管前面的绝对路径是什么"这件事做不到，唯一可移植的办法就是从一开始就不在命令里写绝对路径。

⚠️ **提交进 `.claude/settings.json` 的 allow 规则只放不碰硬件、不改产品文件的命令**（读文件、跑静态检查、编译到本地产物）。**给板子刷固件、生成/替换密钥、往真实设备发升级命令**——这些哪怕本人已经批准过一百次，也不进那份**团队共享**的文件，因为一进去就是给每个 clone 这仓库的人默认放行，没人再会被问一句。这类命令要保留就放个人的 `.claude/settings.local.json`（本机专属、不进 git）。2026-08-23 把 `IAPTool.exe cdc/ether/genkey/sign` 和 `flash_bootloader.py` / `run_*.py` 这批从提交文件里挪回本地文件时发现的。

### 脚本和工具不许放临时目录

**任何要用第二次的东西，都直接写进仓库里的固定位置**，不要放 `%TEMP%` / scratchpad。

放临时目录的东西**不进 git**，下次就找不到了，于是同一个脚本被重写一遍。

| 东西 | 去哪 |
|---|---|
| 测试脚本、自动化工具（烧写、抓串口……） | `$TOOL/TestCase/tools/` |
| 一次性的探查命令（`grep` 一下、看个尺寸） | 不落盘，直接跑 |

> 2026-08-16 犯过：把自动烧写脚本写进 scratchpad，还硬编码了 `D:\ST\STM32CubeIDE_1.10.0` 和工作区绝对路径 —— 换电脑双重报废。**机器相关的路径一律进 `config/machine.py`。**

## 构建

| 目标 | 命令 |
|---|---|
| **工装固件 + 两个 PC 工具（一条命令）** | **`python build.py`** —— `--fw` 只编固件、`--tool` 只编工具、`--boot` 编 bootloader 而不是工装。⚠️ 编固件前 CubeIDE 要关掉 |
| `IAPTool` + `PortTool`（三平台） | `./compile_tool.sh` —— 一次出六个二进制，别手搓 `go build`，输出布局是约定好的 |
| `TestCase`（本机） | `go build -o Output/<GOOS>/TestCase ./TestCase` |

## 面板上的字一律说大白话

**用户 2026-09-11 定：「所有提示信息都要用大白话来说明。」**

⚠️ **而且是中文** —— 用户 2026-09-11 第二次指出：**「页面上英文的地方没有翻译成中文。」** 面板上**人看的字一处英文都不留**：标题、按钮、参数名、下拉选项、读数标签、提示、错误。唯一的例外是**协议原文的回显**（日志窗里的 `pt.start dout ...`、帧原文）—— 那是给对机器的，照原样显示，但它旁边必须有中文说明。

（这一条只管面板。代码注释、`#error` 文案、工具的 stdout/stderr 仍然是英文，见 `~/.claude/rules/language.md`。）

`PortTool` 面板的受众是**硬件工程师**，不是写这套协议的人。所以面板上出现的每一句话：

| 不要 | 要 |
|---|---|
| 直接抄协议字面量（`loop=ctrl`、`mode=extloop`、`mv=1:500`） | 说清它是什么意思、要他做什么 |
| 英文标签（`duty`、`freq`、`hold`、`seq/rx/miss`、`Klemmblock`） | 中文（`占空比`、`频率`、`保持`、`发/收/丢`、`端子排`） |
| 端口内部名（`dout3`、`rs4851`） | 工程师嘴里的名字（`DO3`、`RS485`），出处见 `Hardware/Klemmenbezeichnungen-R.pdf` |
| 裸错误码（`err=0x10000000`） | 译成原因，原始码可以跟在后面 |
| 只给数字 | 带单位、带范围、带"这算过还是不过" |

⚠️ **哪些数字不是判据，要在面板上说出来** —— 例如 `loop=ctrl` 端口回报的那三个计数只说明控制口活着，不是该端口的结论（`$BOOT/docs/design/DECISIONS.md` 第 9 条）。

## 边界

**`IAPTool` 只管上传烧写，不加任何测试专用功能。** 为验证设备行为而存在的东西一律放 `TestCase/`。

**用例的目的是验证出货的那套工具和软件好不好用**（用户 2026-09-03 原话）。客户的入口是 **Arduino IDE 的菜单** —— Tools 选上传方式、点 Upload，IDE 按 `platform.txt` 的配方调**板卡包里的那份 IAPTool**。用例要从那一层进去，不是自己敲 `IAPTool ether`。

⚠️ **每次构建都要把 `Output/<平台>/IAPTool` 拷进板卡包的 `STM32Tools/<版本>/{win,macosx,linux}/`，旧的不留**（用户 2026-09-03 定）。`compile_tool.sh` 末尾已经自动做这件事；漏了由用例 **P11** 抓。不拷的后果实测过：包里那份停在 8-15，所有权那套从写出来起 IDE 一天都用不了。所以凡是客户会用命令做的事，用例就用那条命令去做 —— 不要在用例里另写一份实现，那样测的是用例自己。判据反过来：结果问板子要，不问工具要，工具不能自己证明自己。

四条原则：

1. 测真实代码路径
2. 加密逻辑 import，不重写
3. 反向用例和正向用例一样重要
4. 破坏性用例标 `destructive`
