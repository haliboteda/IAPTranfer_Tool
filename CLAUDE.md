# 开工入口 —— PC 工具与测试资产

**这份文件是给 AI 会话看的。**

这个仓库装两样东西：出货给客户的 **`IAPTool`**（Go，负责把固件烧进板子），以及**整套测试资产 `TestCase/`**（用例、主机侧单元测试、板上 sketch、自动化脚本、验收单）。

> 产品全貌：`<AI-Skills>/OpenPLC/docs/OVERVIEW.md`（本机位置见 `SKILLS_REPO`）。

## 这个仓库自己的东西

| 在哪 | 是什么 |
|---|---|
| 根目录的 `.go` | `IAPTool`：CDC / 以太网两条烧写通道、签名、认证、上传锁 |
| `iapcrypto/` | 加密原语。**测试用例 import 它，不重写** |
| `TestCase/TEST-CASES.md` | **每个用例的判据、前置条件、怎么跑。判据贴着代码走，不搬去 docs/** |
| `TestCase/tools/` | 自动化脚本（烧写、抓串口、跑用例、各种一致性检查） |
| `TestCase/host/` | 不需要板子的检查：假板子、加密交叉验证、主机侧编译真实 bootloader C 源码 |
| `TestCase/onboard/` | 跑在板子上的验证 sketch |
| `TestCase/acceptance/checklist.md` | 出厂与发版验收单 |
| `TestCase/config/machine.{ps1,py}` | **本机路径的唯一出处**，gitignored，且**两份都是生成的** |
| `TestCase/tools/init_machine.py` | 生成上面那两份。**"这台机器有什么"的唯一记录是它里面的 `SETTINGS` 表**，没有模板可抄 |

## 开工前

```powershell
cd TestCase
python tools/selfcheck.py          # 所有不需要板子的检查
python tools/selfcheck.py --list   # 先看它会跑哪几步、各证明哪条需求
```

**改完代码就该跑一遍** —— 上板调试一轮的成本高一个数量级。它的 **ENV** 一步会打出这台机器上每一项工具解析成什么，**缺什么点名说缺什么**，不静默跳过。

配置还没生成过（`config/machine.py is missing`），或者板卡包升级后路径变了，就跑 `python tools/init_machine.py`。那两份配置是**生成的**，没有模板，来源写在它们自己的文件头里。

## 脚本的平台规矩

脚本要求**同时能在 Windows 和 Linux 上跑**。`tools/_common.ps1` 是平台兼容层，写新脚本时：

| 要做的事 | 用这个 | 不要用 |
|---|---|---|
| 判断平台 | `$PLATFORM` | `$IsWindows`（**PowerShell 5.1 下是 `$null`**，判断会反过来） |
| 拼可执行文件名 | `$EXE`、`Get-GoBin`、`Get-IapTool`、`Get-ProgrammerCli`、`Get-CubeIdeExe` | 硬写 `.exe` |
| 临时文件 | `Get-ScratchFile` | `$env:TEMP`（**Linux 上是空的**，路径会塌到文件系统根目录） |
| 相对路径 | `/` 分隔 | `\`（Windows 接受 `/`，Linux 不接受 `\`） |
| Go 输出目录 | `$GOOS_DIR` | 硬写 `windows` |
| 板卡包平台目录 | `$A15_DIR`（`win`/`linux`/`macosx`） | 硬写 `win` |
| CubeIDE 插件后缀 | `$CUBE_PLUG`（`win32`/`linux64`/`macos64`） | 硬写 `win32` |

**遇到一个新的、只有本机知道的路径** —— 加进 `tools/init_machine.py` 的 `SETTINGS` 表（连同探测方式和一段说明），不要硬编码，也不要猜。加进去它就会在每台机器上被自动找出来（理由见 `open_plc_cube_ide/CLAUDE.md` 第七节）。

> ⚠️ 2026-08-19 的双平台改造**只在 Windows 上验证过**（selfcheck 12/12）。Linux 侧是逐条消除平台依赖做的，**没有真机验证**。


### 为什么这条是硬的

**2026-08-19 定。** 目标是：换一台 Linux 机器，clone 下来、装好工具、填一份 `machine.ps1`，全套脚本照跑。

| 要做的事 | 用这个 | 不要用 | 为什么 |
|---|---|---|---|
| 判断平台 | `_common.ps1` 的 `$PLATFORM` | `$IsWindows` | 那是 PowerShell 6+ 才有的变量，**5.1 下是 `$null`**，判断会反过来 |
| 临时文件 | `Get-ScratchFile` | `$env:TEMP` | Linux 上该变量为空，`"$env:TEMP/x.out"` 会**塌成往文件系统根目录写** |
| 可执行文件 | `Get-GoBin` / `Get-IapTool` / `Get-ProgrammerCli` / `Get-CubeIdeExe` / `$EXE` | 硬写 `.exe` | — |
| 相对路径 | `/` | `\` | Windows 的 .NET 路径 API 接受 `/`，Linux 不接受 `\` —— `/` 是唯一两边都对的 |
| 平台目录名 | `$GOOS_DIR` / `$A15_DIR` / `$CUBE_PLUG` | 硬写 `windows` / `win` / `win32` | 同样三个平台，三套工具用三种叫法，集中在一处映射 |

**机器相关的路径一律进 `$TOOL/TestCase/config/machine.{ps1,py}`，而那两份是 `tools/init_machine.py` 生成的** —— 不手写，也没有模板可抄。

⚠️ **判断一个值该不该进配置：另一台同样系统的机器会不会有不同的值？** 不会就不属于那里 —— 那是平台派生量，归 `_common.ps1` / `common.py`。

⚠️ **新的、只有本机知道的路径不许硬编码，也不许猜。** 该往哪儿加、为什么，在 `$TOOL/TestCase/tools/init_machine.py`。

> **2026-08-20 删掉了 `machine.example.{ps1,py}`。** 它们和 `SETTINGS` 表是同一份清单的两个出处。而且模板那套"两个平台的值都给、删掉不用的那套"的用法，**忘记删是最常见的错误** —— 第一次在 Debian 上就踩了，表现是一堆互不相关的 MISSING，加上一句让人去查串口线的错误建议。

⚠️ **这条不止管脚本内部，还管 AI 会话怎么打命令。** 从一个仓库的会话里调另一个仓库的脚本（例如在 `open_plc_cube_ide` 里跑 `IAPTranfer_Tool/TestCase/tools/selfcheck.ps1`），**用相对路径 `../IAPTranfer_Tool/TestCase`，不要绝对路径**。第三节那张兄弟目录表已经把布局钉死了，相对路径换机器天然成立；绝对路径（`E:\WorkSpace\...`）只在这台机器上对，写进 `.claude/settings.local.json` 的 allow 列表里，换机器就是一条永远不会再命中的死记录。

**2026-08-23 实测：allow 列表不支持在字符串中间用 `*` 匹配任意前缀**，只有结尾通配符是文档确认支持的（`Bash(git *)` 这种）。所以"允许这条命令、不管前面的绝对路径是什么"这件事做不到，唯一可移植的办法就是从一开始就不在命令里写绝对路径。

⚠️ **提交进 `.claude/settings.json` 的 allow 规则只放不碰硬件、不改产品文件的命令**（读文件、跑静态检查、编译到本地产物）。**给板子刷固件、生成/替换密钥、往真实设备发升级命令**——这些哪怕本人已经批准过一百次，也不进那份**团队共享**的文件，因为一进去就是给每个 clone 这仓库的人默认放行，没人再会被问一句。这类命令要保留就放个人的 `.claude/settings.local.json`（本机专属、不进 git）。2026-08-23 把 `IAPTool.exe cdc/ether/genkey/sign` 和 `flash-bootloader.ps1` / `run-*.ps1` 这批从提交文件里挪回本地文件时发现的。

### 脚本和工具不许放临时目录

**任何要用第二次的东西，都直接写进仓库里的固定位置**，不要放 `%TEMP%` / scratchpad。

放临时目录的东西**不进 git**，下次就找不到了，于是同一个脚本被重写一遍。

| 东西 | 去哪 |
|---|---|
| 测试脚本、自动化工具（烧写、抓串口……） | `$TOOL/TestCase/tools/` |
| 一次性的探查命令（`grep` 一下、看个尺寸） | 不落盘，直接跑 |

> 2026-08-16 犯过：把自动烧写脚本写进 scratchpad，还硬编码了 `D:\ST\STM32CubeIDE_1.10.0` 和工作区绝对路径 —— 换电脑双重报废。**机器相关的路径一律进 `config/machine.ps1`。**

## 构建

| 目标 | 命令 |
|---|---|
| `IAPTool`（三平台） | `./compile_tool.sh` —— 别手搓 `go build`，输出布局是约定好的 |
| `TestCase`（本机） | `go build -o Output/<GOOS>/TestCase ./TestCase` |

## 边界

**`IAPTool` 只管上传烧写，不加任何测试专用功能。** 为验证设备行为而存在的东西一律放 `TestCase/`。

四条原则，**完整版和每条背后踩过的坑在 `open_plc_cube_ide/docs/test/CASE-DESIGNS.md`**：

1. 测真实代码路径
2. 加密逻辑 import，不重写
3. 反向用例和正向用例一样重要
4. 破坏性用例标 `destructive`
