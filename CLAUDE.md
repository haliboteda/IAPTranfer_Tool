# 开工入口 —— PC 工具与测试资产

**这份文件是给 AI 会话看的。**

这个仓库装两样东西：出货给客户的 **`IAPTool`**（Go，负责把固件烧进板子），以及**整套测试资产 `TestTool/`**（用例、主机侧单元测试、板上 sketch、自动化脚本、验收单）。

## ⚠️ 共享文档不在这个仓库里

产品的需求、架构、硬件事实、设计决策、协作规矩，**全部在 `open_plc_cube_ide/docs/` 下**，那里是唯一出处。这份文件不抄，只指路。

```
git clone git@github.com:haliboteda/open_plc_cube_ide.git
```

然后读它根目录的 `CLAUDE.md` —— **换机器要 clone 什么、装什么、配什么，那一份写全了**。

## 这个仓库自己的东西

| 在哪 | 是什么 |
|---|---|
| 根目录的 `.go` | `IAPTool`：CDC / 以太网两条烧写通道、签名、认证、上传锁 |
| `iapcrypto/` | 加密原语。**测试用例 import 它，不重写** |
| `TestTool/TEST-CASES.md` | **每个用例的判据、前置条件、怎么跑。判据贴着代码走，不搬去 docs/** |
| `TestTool/tools/` | 自动化脚本（烧写、抓串口、跑用例、各种一致性检查） |
| `TestTool/host/` | 不需要板子的检查：假板子、加密交叉验证、主机侧编译真实 bootloader C 源码 |
| `TestTool/onboard/` | 跑在板子上的验证 sketch |
| `TestTool/acceptance/checklist.md` | 出厂与发版验收单 |
| `TestTool/config/machine.ps1` | **本机路径的唯一出处**，gitignored。模板是 `machine.example.ps1` |

## 开工前

```powershell
cd TestTool
Copy-Item config/machine.example.ps1 config/machine.ps1   # 然后编辑
pwsh ./tools/selfcheck.ps1        # Windows 上也可以 .\tools\selfcheck.ps1
```

`selfcheck.ps1` 是**所有不需要板子的检查**，改完代码就该跑一遍 —— 上板调试一轮的成本高一个数量级。它的 **A0** 会打出这台机器上每一项工具解析成什么，**缺什么点名说缺什么**。

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

**遇到一个新的、只有本机知道的路径** —— 加进 `config/machine.example.ps1` 并告诉用户，不要硬编码，也不要猜。

> ⚠️ 2026-08-19 的双平台改造**只在 Windows 上验证过**（selfcheck 12/12）。Linux 侧是逐条消除平台依赖做的，**没有真机验证**。

## 构建

| 目标 | 命令 |
|---|---|
| `IAPTool`（三平台） | `./compile_tool.sh` —— 别手搓 `go build`，输出布局是约定好的 |
| `TestTool`（本机） | `go build -o Output/<GOOS>/TestTool ./TestTool` |

## 边界

**`IAPTool` 只管上传烧写，不加任何测试专用功能。** 为验证设备行为而存在的东西一律放 `TestTool/`。

三条原则（完整版和踩过的坑在 `open_plc_cube_ide/docs/BUILD-AND-TEST.md`）：

1. **测真实代码路径。** 需要"传输进行中"的用例把 `IAPTool` 当子进程拉起来跑真实烧写，不自己实现传输
2. **加密逻辑 import，不重写**
3. **破坏性用例标 `destructive`**，`all` 模式自动排到最后

## 语言

代码注释、脚本的 stdout/stderr、`README.md` 一律**英文**；本文件和 `docs/` 下的项目笔记用中文。
