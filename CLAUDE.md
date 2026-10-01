# 开工入口 —— IAPTool

**这份文件是给 AI 会话看的。**

这个仓库装出货给客户的 **`IAPTool`**（Go，负责把固件烧进板子），以及它自己的单元测试。要 bootloader、板卡包或真板子的测试在 `OpenPLC_Test`（`$TEST`，决策 78）。

> **产品文档在 `OpenPLC_Docs`**（`$PROD`）—— 全部文档和待决的问题，入口它的 `README.md`。

## 这个仓库自己的东西

| 在哪 | 是什么 |
|---|---|
| 根目录的 `.go` | `IAPTool`：CDC / 以太网两条烧写通道、签名、认证、上传锁 |
| `internal/serialx/` | 串口层：开口、重试、枚举（带 VID/PID）。macOS 上不开 cgo，VID/PID 由系统自带的 `ioreg` 补 —— 见 `enum_darwin.go` |
| `iapproto/` | 和 bootloader 说话要一致的协议常量（数据帧大小、命令超时）与绑物理网卡的 UDP / TCP 拨号（决策 51）。公开包：`$TEST` 的 `TestCase.exe` import 它，不手抄（决策 77） |
| `netiface/` | 物理网卡判定（决策 51）。公开包，理由同上（Go 不许别的 module 引用 `internal/`） |
| `iapcert/` | 证书与签名原语。**测试 import 它，不重写** |
| `tests/` | 本仓的测试：`iapcert/`（T1-15）、`crypto_ref/`（T1-19 / T1-20，独立实现交叉验证）、`selfcheck.py` |
| `tools/install_tool.py` | 把编好的 IAPTool 拷进板卡包；`compile_tool.sh` 末尾自动调 |

## 开工前

```
python tests/selfcheck.py          # go vet、go test（T1-15、T1-35）、T1-19 / T1-20
python tests/selfcheck.py --list   # 每一步证明哪条需求
```

只要 Go 和 Python 3，**没有本机配置文件**：板卡包按平台默认的 Arduino15 位置找（`ARDUINO15` 可覆盖），Git Bash 按 `GIT_BASH` / PATH / 常见安装位置找。

## 构建

| 目标 | 命令 |
|---|---|
| `IAPTool`（三平台）并装进板卡包 | **双击 `build.cmd`**，或者 `python build.py`（调 `./compile_tool.sh`），别手搓 `go build` |

bootloader 用 STM32CubeIDE 编；测试要的命令行编译在 `$TEST`。

## 边界

**`IAPTool` 只管上传烧写，不加任何测试专用功能。** 为验证设备行为而存在的东西一律放 `$TEST`。

客户的入口是 **Arduino IDE 的菜单**：IDE 按 `platform.txt` 的配方调**板卡包里的那份 IAPTool**。⚠️ **每次构建都要把 `Output/<平台>/IAPTool` 拷进板卡包的 `STM32Tools/<版本>/{win,macosx,linux}/`，旧的不留**（用户 2026-09-03 定）。`compile_tool.sh` 末尾自动做；漏了由 `$TEST` 的 **P11** 抓。不拷的后果实测过：包里那份停在 8-15，所有权那套从写出来起 IDE 一天都用不了。

⚠️ **提交进 `.claude/settings.json` 的 allow 规则只放不碰硬件、不改产品文件的命令**（读文件、跑静态检查、编译到本地产物）。给板子刷固件、生成 / 替换密钥、往真实设备发升级命令的放个人的 `.claude/settings.local.json`：团队共享的那份一进去，就是给每个 clone 的人默认放行。
