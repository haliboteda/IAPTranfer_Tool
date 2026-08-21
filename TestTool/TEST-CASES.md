# TestTool

**这个产品的测试与验收总入口。** 和 IAPTool 分开：**IAPTool 只负责上传烧写**，所有为了验证设备行为而存在的东西放在这里。

需要"传输进行中"的用例不会自己实现传输，而是**把 IAPTool 当子进程拉起来**跑真实烧写 —— 被测的始终是出货代码路径，不是测试代码里的仿制品。

## 目录结构

```
TestTool/
├── *.go                  ← 设备行为用例（T/S/N 系列），package main
├── config/
│   ├── machine.ps1       ← 本机路径（gitignore）。**生成的，不要手抄**
│   └── machine.py        ← 同上，Python 侧。M7 期间两份并存，同一处生成
├── requirements.txt      ← 唯一的 pip 依赖：pyserial
├── tools/                ← 自动化工具，本身不是测试
│   ├── init_machine.py   ← ★ 换电脑第一条命令。先探测，搜不到才问你
│   ├── test_init_machine.py ← init_machine 提问逻辑的单元测试
│   ├── _common.ps1       ← 共用：读 config、找工具链、开串口、判目标电压
│   ├── common.py         ← 同上，Python 侧。`python tools/common.py --probe` = A0
│   ├── selfcheck.ps1     ← ★ 所有不需要板子的检查，一条命令
│   ├── check-version-sync.ps1  ← P1  版本号三处一致
│   ├── check-mirror-sync.ps1   ← P2  跨仓镜像 8 锚点 + 备份寄存器占用
│   ├── check-core-sync.ps1     ← P3  core live vs git 仓库
│   ├── check-public-root.ps1   ← P6  公开根指纹没漂移
│   ├── check_*.py              ← 上面四个的 Python 版（M7 第 2 步）
│   ├── m7-compare.ps1          ← ★ 两版输出逐字节比对
│   ├── m7-compare-faults.ps1   ← ★ 注入故障后再比一次
│   ├── flash-bootloader.ps1
│   └── serial-watch.ps1
├── host/                 ← 不需要板子，纯主机跑
│   ├── iapcrypto/        ← H1  密钥派生与挑战应答的 Go 单元测试
│   ├── bootloader_unit/  ← H2  用 stub 编译真实 bootloader 源码的 C 单元测试
│   ├── fakeboard/        ← K1–K6  IAPTool 传输前的密钥匹配决策，六种情况
│   └── crypto_ref/       ← X1/X2  SHA-256 与 ECDSA 的独立实现交叉验证
├── onboard/              ← 需要烧到板子上跑
│   └── rs232/SerialPort/ ← O1  UART + CDC 回显 sketch
└── acceptance/
    └── checklist.md      ← 出厂 / 量产验收单
```

> **需求清单和完整的覆盖矩阵在 `open_plc_cube_ide/docs/handover/`** —— [REQUIREMENTS.md](../../open_plc_cube_ide/docs/handover/REQUIREMENTS.md) 说要做到什么，[TEST-PLAN.md](../../open_plc_cube_ide/docs/handover/TEST-PLAN.md) 说每条用例覆盖哪条需求、最近一次跑出什么结果、还欠哪些用例。
> **本文件只管判据和运行方法**（贴着代码走，跨仓不搬）。

⚠️ **机器相关的路径只允许出现在 `config/machine.{ps1,py}`。** 脚本里写死绝对路径、或用 `..\..\..\` 数上去，换台电脑或挪个目录就废 —— 这两种都犯过。

⚠️ **`init_machine.py` 有单元测试**：`python tools/test_init_machine.py`（**32 个用例**，四组）。

| 组 | 测什么 | 为什么只能这么测 |
|---|---|---|
| tables | `SETTINGS` / `PREREQS` / `EXAMPLES` 的列是否齐 | 三张手写表，最常见的编辑错误是加了一行漏一列；不测的话报错发生在你不在场的那台机器上 |
| ports | `detect_log_ports()` 的 **macOS 分支** | 本项目没有 mac，所以文件系统是假的，只测排序与去重逻辑 |
| claude dirs | `--write-claude-dirs` 的合并、`ignored_by_own_rules()` 的护栏 | 它改的那个文件装着几百条手工批准的权限规则，**"没弄丢东西"就是被测的性质** |
| ask_for | 引号剥离、`~` 展开、安装根目录校验 | 提问按定义在自动化里跑不到（只在"stdin 是终端且环境无自动化标记"时发生），只能替换 `input()` |

退出码：0 全过，1 有失败，2 全过但 ask_for 那组因这台机器没有真 CubeIDE / Arduino IDE 而跳过 —— **不假装通过**。

⚠️ **`--prereqs` 和 A0 的分工**：`--prereqs` 看 PATH 上的运行时（git / Python / Go / cc / PowerShell / pyserial）在不在，`selfcheck.ps1` 的 A0 看本机路径解析成了什么。前者必须在 Python 里，因为 **A0 要 PowerShell 才跑得起来，而 pwsh 恰好是 Debian / macOS 上最可能缺的那一个**。

⚠️ **那两个文件是 `tools/init_machine.py` 生成的，不要手写、也没有模板可抄。** 需要一个新的本机路径时，把它连同探测方式加进那个脚本的 `SETTINGS` 表 —— 那里是"这台机器有什么"的唯一记录。以前的 `machine.example.*` 已删除：它和 `SETTINGS` 是同一份清单的两个出处，留着必然漂移。

## 快速开始

```powershell
# 换台电脑时最省事的一条：/openplc:init（Claude Code skill，走完下面全部步骤）
# 手工的话：

# 缺哪些运行时，以及这台系统上怎么装
python tools\init_machine.py --prereqs      # Linux 上是 python3

# 一次性：探测本机路径，生成 config/machine.ps1 和 machine.py
python tools\init_machine.py      # Linux 上是 python3

# 让一个仓库里的 Claude 会话读得到兄弟仓库，不必一路批权限
python tools\init_machine.py --write-claude-dirs

# 所有不需要板子的检查。改完代码先跑这个，全绿了再考虑上板
.\tools\selfcheck.ps1

# 构建 bootloader、烧写、抓启动日志、给判定（CubeIDE 必须关闭）
.\tools\flash-bootloader.ps1

# 只看串口，不碰板子
.\tools\serial-watch.ps1
```

`selfcheck.ps1` 缺什么会报 `SKIP` 并说清缺什么，**不会静默跳过** —— 一个被悄悄跳过的检查会被读成通过，那比没有这个检查更糟。

## 编译

```sh
# 在 IAPTranfer_Tool/ 下
go build -o Output/windows/TestTool.exe ./TestTool
```

和 IAPTool 共用一个 Go module，不引入额外依赖，也不会让 `IAPTool.exe` 变大。

## 运行

```sh
TestTool <case-id|all> --ip=<addr> [--port=56865] [--bin=<file.bin>] [--iaptool=<path>]
```

- `--ip` 必填。设备 IP 从串口日志的 `[NET]` 行读，或用 `IAPTool ether` 的广播发现看。
- `--bin` 只有 T3 需要。
- `--iaptool` 默认找 `Output/windows/IAPTool.exe`。
- 退出码：全过 0，有失败 1，参数错 2。

## 用例

### TCP 会话规则（`tcp_session.go`）

设备一次只服务一个客户端，且空闲客户端不能永久占住端口。

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **T1** | 空闲连接 60s 后被踢 | 设备停在 bootloader | 连上后保持沉默，在 ~60s（容差 +15s）内被对端关闭 |
| **T1b** | 空闲连接 50s 内**不**被踢 | 设备停在 bootloader | 沉默 50s 后连接仍在，且 `ping` 仍答 `OK` |
| **T2** | 已有连接时第二个连接被拒 | 设备停在 bootloader | 第二个连接被拒（或虽接上但不被服务），**且第一个连接不受影响** |
| **T3** | 传输进行中的第二个连接不打断传输 | 设备停在 bootloader，需 `--bin` | IAPTool 报告传输完成且退出码 0，闯入的连接未被服务 |
| **T4** | 第一个连接正常关闭后能再连 | 设备停在 bootloader | 关闭后重连成功并被服务 |

**T3 是破坏性用例** —— 它真的烧写，跑完板子会重启进 app，不再停在 bootloader。`all` 会自动把破坏性用例排到最后，否则它后面的用例会全部假失败。单独跑时请自己注意顺序。

已观察到的一个行为差异：设备**空闲**时第二个连接是"TCP 接上但不被服务"（T2），而**传输进行中**是直接 RST 拒绝（T3）。两种都满足"一次只服务一个客户端"，但状态不同表现不同。

T1b 和 T4 是反向用例。它们防的是两类只在现场暴露的毛病：踢得太早（连接莫名其妙断），以及"拒绝第二连接"的状态没复位（第一次之后再也连不上）。这类"不该发生的事没发生"最容易在测试里被漏掉。

### UDP 发现（`udp_discovery.go`）

每一次以太网升级都从一条发现回复开始，所以发现一旦不稳，现场看到的是"板子不见了"。

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **N1** | 四个关键词都应答 | 设备在线 | `openplc_server_where_r_y` / `DISCOVER` / `openplc_discover` / `ping` 全部有回复 |
| **N2** | 多轮间隔查询都应答 | 设备在线 | 6 轮全部有回复 |
| **N3** | 回复落在工具的超时之内 | 设备在线 | 20 轮全部在 2s（IAPTool 的 `CommandTimeout`）内返回 |
| **N4** | 长时间浸泡下发现依然可靠 | 设备在线，`--minutes=N`（默认 10） | 整段时间内零次无应答 |
| **N5** | 泛洪被封顶，且封顶不会把发现打死 | 设备在线 | 以约 500 次/秒猛打 3 秒，回复速率不超过 50/s 的上限；**且随后正常查询仍能应答** |

N5 的第二半判据同样重要：**上限要是限速器，不能是保险丝**。一个把后续正常发现也一起掐死的上限，比没有上限更糟。

**N4 是为罕见间歇故障准备的**。2026-08-15 观察到 UDP 发现偶发无应答（同期 ICMP/TCP 全程正常），但 N1/N2/N3 这类短测反复跑都是干净的 —— 短促的抽查抓不到它。N4 持续查询并记录每次失败的发生时刻和距上次失败的间隔，跑在后台，出问题时有据可查。

**N3 是用来区分两种同样表现为"没反应"的情况的**：板子根本没回，还是回晚了。IAPTool 每次查询都新建一个 socket 且用临时端口，超时后就关掉 —— 迟到的回复会打在已关闭的端口上被丢弃，看起来和"板子不在"一模一样。N3 用 10s 的耐心去等，把这两者分开。

⚠️ **bootloader 和 app 各跑一套独立的 UDP 服务**，一侧的结果不能代表另一侧。N3 会打印应答方的身份串（`BOOTLD` / `BOOTLD-INVALID` / `CUSAPP`），看结果时务必先确认测的是哪一侧。

### 签名校验（`signature.go`）

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **S1** | 签名无效的镜像被拒绝 | 设备停在 bootloader，需 `--bin` 和 `--password-file` | 传完后设备回 `Signature Failed`（或 `No Signature`） |
| **S2** | 被**别的密钥**签过的镜像被拒绝 | 同 S1，另需 `--iaptool`（用它生成临时密钥并签名） | 同上。**外加**上传前 `getpubkey` 必须和临时密钥不同 |
| **S3** | **已装好的** app 被改坏 → 启动期拒绝 | 板上有能启动的 app、ST-Link、**一个已签名的恢复镜像** | `metadata present` + `App signature invalid or absent`，且**没有** `** APP Mod` |
| **G1** | 被拒绝的上传**不破坏已装好的 app** | 紧接 S1 之后复位 | 下次启动出现 `** APP Mod ...`，**不是** `no valid application`。用 `tools/run-case.ps1 -Case S1 -ThenReset` 跑 |

```powershell
.\tools\run-s3.ps1 -Bin <app.bin>       # 破坏 + 判定 + 自动恢复
```

**S3 是破坏性的，但自带恢复，而且恢复路径先证明后破坏** —— 脚本第 1/4 步先烧一次恢复镜像并看着板子起来，不通就拒绝往下走。

⚠️ **S1 已经不能代替 S3。** SDRAM staging 之后，失败的上传根本不碰 app 区（那正是 G1），所以 S1 证明不了"已装好的 app 每次启动都被重新校验"。

⚠️ **改一个字节要先擦扇区再写回完整镜像，不能直接改那个字节。** STM32H7 的 flash 每 256 位带 ECC，同一个字编程两次会让 ECC 变成两次的按位与，读出来是不可纠正错误并把 CPU 打进 fault —— bootloader 会在算哈希时崩掉，而不是干净地报签名不过。

⚠️ **`App signature invalid or absent` 和 `no valid application` 出自同一个分支**（`IAPServer/IAP_server.c:482-494`），必然同时出现。区分"app 坏了"和"metadata 也没了"的是 `Bootloader state:` 那行的 `metadata present` / `absent`。

**S1 和 S2 共用同一条上传路径**（`uploadWithSignature`）：认证 HMAC、CRC、分块 framing 全部是 IAPTool 会发的东西，**唯一不同的是那 64 字节签名**。S1 用 64 个零字节（没有任何密钥能产生），S2 用 `IAPTool sign` 拿一把临时密钥真实签出来的东西（格式完全合法，只是签方不对）。

⚠️ **S2 上传前必须先 `getpubkey` 确认板子信的不是这把临时密钥。** 密钥是每次现生成的，撞上是不可能事件 —— 这道闸防的是将来有人把它改成固定密钥文件：那样用例不会报错，它会**真的把 `--bin` 指的东西刷进板子**。

### 所有权（`tools/run-takeown.ps1`，需求 C10）

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **OW1** | 认领把板子绑到一把新密钥上 | 板子停在 bootloader，**且这次启动按住过 BOOT0** | `takeown` 回 `OK`；`getpubkey` 返回新密钥；复位后仍然认得，公开根告警消失 |
| **OW1-neg** | BOOT0 没按时认领被拒 | 停在 bootloader，**没按 BOOT0**（用 `enter-bootloader.ps1` 进） | 回 `Refused`，`getpubkey` **一字节不变** |

| **OW2** | 换 owner：现任签名才算数 | 板子已被一把**你持有私钥**的密钥认领 | 正确签名 → `OK` 且 generation +1；坏签名 → `Refused` 且什么都没变 |
| **OW2-attack** | 无签名的高 generation 记录**夺不走**板子 | 同上 | 扫描器看得见那条记录，但 `getpubkey` 仍返回原主人 |
| **OW3** | 恢复出厂，然后能重新认领 | 板子已被认领，**有人在板子旁** | 按住 BOOT0 十秒 → `FACTORY RESET DONE` → 回落内置根、公开根告警回来 → 再 `takeown` 能成功 |

⚠️ **OW3 的判据必须包含"能重新认领"。** 只验"回落内置根"是不够的：链规则曾经会拒绝 cleared 之后的新认领记录，那样恢复出厂等于**把板子永久钉死在公开根上**，比不做恢复出厂更糟。

```powershell
.\tools\run-takeown.ps1 -ExpectRefused          # 认领负向，不需要人
.\tools\run-takeown.ps1                         # 认领正向，需要有人按住 BOOT0
.\tools\run-setowner.ps1 -CurrentKey a.pem      # 换 owner，不需要人
.\tools\run-setowner.ps1 -CurrentKey a.pem -BadSignature
.\tools\inject-owner-record.ps1 -Key <hex> -AlsoUnsigned 9   # 夺取攻击
```

⚠️ **换 owner 不需要 BOOT0**，这是设计不是疏漏：现任签名本身就是授权，远程交接要支持。物理门只管**没有签名可验**的操作（首次认领、恢复出厂）。

⚠️ **OW2-attack 是这一组真正的判据。** 只验"正确签名能换、坏签名不能换"是不够的 —— 真正的洞是"按 generation 最大的赢"：那样任何能追加记录的人写一条更高 generation 的**无签名**记录就能夺走一块已认领的板子。判据必须是**板子仍然认原主人**。

⚠️ **认领是刻意做成不好撤销的。** 第 6 步的恢复出厂还没实现，现在唯一的退路是 **ST-Link 重烧 bootloader** —— owner 记录和 bootloader 同扇区，擦掉重写就一起没了（`.\tools\flash-bootloader.ps1`）。

⚠️ **判据必须包含"旧密钥签的固件被拒"。** 2026-08-18 首次跑就抓到：认领改了 `getpubkey` 的回答和告警文字，却没改实际验签用的密钥（`fw_verify_signature()` 写死了 `fw_public_key`）——**板子一边说"只认新主人"，一边照跑任何人签的固件**。只看日志的判据会给出干净的通过。

### 认证与重放（`nonce_replay.go`）

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **AU1** | nonce 不重复，且**掉电后不从头开始** | 设备停在 bootloader；**要人工断电一次**；VBAT 电池在位 | 两阶段所有 nonce 互不相同；阶段内计数器恰好 +1；断电后的第一个计数器**严格大于**断电前最后一个 |

板子没有硬件随机数，nonce 是 `计数器(4B) ‖ UID字0(4B) ‖ tick(4B) ‖ 0(4B)`（`IAPServer/iap_auth.c`）。**撑住重放保护的是唯一性，不是不可预测性** —— 攻击者真正需要的是 HMAC 密钥，那个从观察 nonce 得不到。唯一性完全依赖那个计数器活在 VBAT 供电的 RTC 备份寄存器里。

```powershell
.\tools\run-au1.ps1                 # 编排两个阶段，中间提示你拔电
.\tools\run-au1.ps1 -Resume         # 阶段 1 已经跑过了，直接等断电
```

⚠️ **必须真断电，不能按复位。** 复位根本不碰备份域，所以只做复位的话，**一块 VBAT 已经没电的板子照样能通过** —— 而那正是这个用例要抓的板子。脚本靠板子自己的 UDP 发现应答判断断电是否真的发生（连续三次无应答才算），不靠人按回车。

⚠️ **`nonce counter = N` 那行只在 bootloader 停下来时才打。** 板上有有效 app 的话，上电那次直接交权给 app，走不到那句 —— 它会出现在随后 `enter-bootloader` 的日志里。交叉核对要两份日志一起翻。

⚠️ **`enter-bootloader.ps1` 的输出用 `6>&1` 抓，不是 `2>&1`。** 它全部走 `Write-Host`，那是信息流不是成功流；用 `2>&1` 抓到的是空字符串，而文字照样出现在控制台上。

**IAPTool 按设计做不出这个用例** —— 它的 `getpubkey` 预检会在传输前就拦掉板子不会接受的镜像。所以 S1 直接驱动协议，但**加密部分 import 出货代码里的 `IAPTool/iapcrypto`**，不是另写一份：只有*签名*是故意错的（64 个零字节），认证 HMAC、CRC、分块framing 全部和 IAPTool 发的一模一样。

命令本身必须通过认证 —— 如果连命令都被拒，就什么也证明不了，这一点在代码里显式检查。

S1 还会把镜像改掉一个字节，使它和板子上已存的 metadata 不匹配。这样一次运行同时覆盖签名路径的两半：**上传时**的校验（设备回 `Signature Failed`），和**下次复位启动时**的校验（日志应出现 `metadata present` + `App signature invalid or absent`）。

~~**S1 是破坏性的**：跑完板子会拒绝启动 app，必须用 IAPTool 正常烧一次才能恢复。~~
**2026-08-17 起不再是** —— SDRAM staging 之后 app 区根本没被碰过，见下面的 G1。

## 怎么让设备停在 bootloader

T1–T4 和 S1 都要求设备处于 bootloader 且以太网已起。三种办法：

1. **`tools\enter-bootloader.ps1`** —— 全自动，不需要碰板子。**推荐**
2. **按住 BOOT0 复位** —— 日志出现 `** UPLOAD Mod ... (BOOT0 held)`
3. 让 app 收到认证过的 UDP reboot（`IAPTool ether` 的第一步就是这个），但它随后会真的上传

第 1 种的原理：给 IAPTool 一个**大于 app 区**的镜像。它第一步先把 app 重启进 bootloader，然后才递上镜像 —— 而板子在尺寸检查（`IAPServer/IAP_server.c:206`）就拒了，**擦除和暂存都还没发生**。于是板子停在 bootloader，一个字节都没写。

**全程只走出货代码**：IAPTool 真实的认证 reboot + 板子真实的尺寸检查。协议部分没有任何重新实现 —— 这也是为什么不用 SWD 去戳 SRAM4 的交接记录（那等于在测试工具里抄一份产品格式）。

注意：通过 **CDC** 请求进入的上传模式（`BOOT_REQ_CDC`）**不会启动以太网**，那种状态下这些用例连不上，这是设计如此的通道隔离，不是故障。

## 主机侧测试（`host/`，不需要板子）

跑得快、随时能跑，**改完代码先过这一层再上板**。

| 目录 | 怎么跑 | 覆盖什么 |
|---|---|---|
| `host/iapcrypto/` | 在 `IAPTranfer_Tool/` 下 `go test ./TestTool/...` | HMAC 原语对 RFC 4231 向量；派生公式 `HMAC-SHA256(password, machineID)`；同 UID 稳定、异 UID 必不同；一次完整挑战应答双方独立算出同一个 HMAC |
| `host/bootloader_unit/` | `.\build.ps1` 或 `./build.sh`，需要 gcc/clang | 用 stub 在主机上编译**真实的** `sha256.c` / `iap_keyderive.c` / `iap_auth.c` 并跑断言 |
| `host/fakeboard/` | `.\run-cases.ps1`，需要 python | **K1–K6** IAPTool 在传输开始前的密钥匹配决策，六种情况。**每种在真板子上都要换一把 bootloader 密钥才能构造** |
| `host/fakeboard/` | `.\run-downgrade.ps1`，需要 python | **DG1** 降级拦截，五种情况。每条都额外断言**板子有没有真的收到 `flash` 命令** —— 只看工具打了什么，挡不住"打印了拒绝然后照样上传" |
| `host/crypto_ref/` | `.\run-checks.ps1`，需要 python | SHA-256 构造对 hashlib（309 向量）；IAPTool 真实签名交给一份独立的纯算术 P-256 验证器 |
| `host/variant_check/` | `.\build.ps1`，需要 arduino-cli | **P4** Arduino 变体头的编译期断言。目前一个：FMC 保留脚表（39 个）自洽。**编不过就是变体头坏了，不是 sketch 坏了** |
| `host/examples_build/` | `.\build.ps1`，需要 arduino-cli | **P5** 编译 core 自有库的**每一个 example**。⚠️ **约十分钟，故意不进 selfcheck** —— 见下 |

### P5 · example 不能腐烂

**什么时候跑**：改了 `open_plc_arduino` 的任何库之后，以及发版前。**不在 `selfcheck` 里** —— selfcheck 是"改完代码就跑"的东西，往里加十分钟只会让人不跑它。

```powershell
.\host\examples_build\build.ps1              # 全部
.\host\examples_build\build.ps1 -Only SDRAM  # 只挑一个库
```

**为什么值得有**：example 是新用户编译的第一个东西，也是最后一个有人回头看的东西。API 改了名，example 还引用旧名，**除非有人正好去打开它，否则永远没人知道** —— 别的检查一条都盖不到，因为 example 不属于任何应用的构建。

它上线第一次跑就抓到了一个真问题：`OpenPLC_KNX` 的 `getGroupObject()` 守卫漏了 `MASK_VERSION == 0x5780`，**而那是默认的 knxrole**。后果是默认配置下用不了 group object（KNX 应用编程的核心），且库自带的两个 example 用默认 FQBN 编不过。已修。

⚠️ **只编译本项目自己的库**（`OpenPLC_*`）。上游 STM32duino 的库带着几百个给别的板子写的 example，编它们只会报出一堆没人打算修的失败。

每个目录有自己那份说明（`HOST-C-TESTS.md` / `KEY-MATCH-AND-DOWNGRADE.md` / `CROSS-CHECK.md`），写清"为什么这条不能在板子上测"。**文件名要说出内容 —— 不要再叫 `README.md`。**

`tools/` 下还有四个纯静态检查，不碰任何代码执行：`check-version-sync.ps1`（版本号三处一致）、`check-mirror-sync.ps1`（跨仓镜像 9 个锚点 + RTC 备份寄存器占用）、`check-core-sync.ps1`（core live 与 git 仓库）、`check-public-root.ps1`（**P6**）。前三个对应发版检查单的 B1 / B2 / B3，以前是人工核对。

### P6 · "信任公开根"的告警不能失灵

bootloader 每次启动会在**当前生效的根就是随项目发布的那把公开根**时告警。它靠编进 `IAPServer/owner_slot.c` 的一个 SHA-256 指纹常量认出那把密钥。

**指纹是常量不是构建时算的** —— 算的话比较永远成立，客户用自己密钥编的板子也会被告警，而**一个所有人都学会忽略的告警等于没有告警**。代价是项目自己轮换默认密钥时必须同步更新它，否则出厂板静默地不再告警。P6 就是比对这两者。

⚠️ P6 还会检查 **`Debug/*.bin` 里到底有没有 `fw_pubkey.inc` 那把密钥**。因为换密钥有个静默陷阱：**普通复制/还原会保留源文件时间戳**，还原回来的 `.inc` 比 `.o` 旧，make 判定不用重编，于是**固件构建正常、启动正常、却带着旧的信任根**。2026-08-18 当场踩到过。

⚠️ **在客户的 fork 里这两者本来就该不一样** —— 那正是"有自己的根"的含义。这条检查属于本仓库，这里的默认密钥按定义就是公开的那把。

### M7 · PowerShell 版和 Python 版必须给出同一个结论

**只在 M7 改写期间存在**（见 `open_plc_cube_ide/docs/handover/Todo/M7-python-scripts.md`）。两个脚本，都不碰板子，随时可跑：

```powershell
.\tools\m7-compare.ps1            # 两版都当子进程跑，输出逐字节比对
.\tools\m7-compare-faults.ps1     # 逐个注入故障，每次再比一遍
```

| | 判据 |
|---|---|
| `m7-compare.ps1` | 每一对的 **stdout 完全相同**，退出码相同。目前 4 对：version / mirror / core / public-root |
| `m7-compare-faults.ps1` | 7 个故障用例，每个都要两版**同样红**；跑完 `$BOOT_REPO` 和 `$CORE_REPO` 的 `git status` 必须干净 |

⚠️ **`m7-compare.ps1` 单独跑不算验收。** 一个什么都不检查、只打印同样文字的 Python 脚本能轻松通过它 —— 这就是 M5 那次"用例在未修的代码上跑出干净通过"的同一个形态。**故障注入那份才是判据**，它逼出的是 DIFF（单值和多段两种格式）、SKIP、ONLY-LIVE、ONLY-REPO 这些只有真在读文件才会走到的分支。

⚠️ `m7-compare-faults.ps1` **会临时改 `$BOOT_REPO` 里的真实文件**（`RELEASE-NOTES.md`、`owner_slot.c`、`udp_server.c`、`fmc.c`），在 `finally` 里还原，最后自检两个仓库是否干净。**跑之前先把手头改动提交或 stash** —— 否则最后那一步分不清脏的是你的改动还是没还原干净。中途被打断时，`git -C $BOOT_REPO status` 就是恢复的起点。

⚠️ `Known` 列表里的每一条"允许不同"都必须写清理由，且命中时会打印出来。**不要用放宽比对来消差异** —— 现在只有一条：`-Print` / `--print`，因为两边开关名字确实不同。

> **`host/iapcrypto/` 之前是独立 Go module，待在 `Hardware/TestCase/` 下，靠 `replace IAPTool => ../../../IAPTranfer_Tool` 相对路径挂过来。**
> 结果是 `go test ./...` 永远扫不到它 —— 2026-08-16 并入本 module 时才发现它**早就编译不过**（`iapcrypto.FixedPassword` 在密码改成运行时加载后就没了）。
> **教训：测试放在主构建扫不到的地方，等于没有测试。**

## 板上测试（`onboard/`）

| 目录 | 是什么 | 怎么用 |
|---|---|---|
| `rs232/SerialPort/` | UART + USB-CDC 回显 sketch | 用 Arduino IDE/CLI 编译上传，往端子 C05/C06 发字符看回显 |
| `rs232/M5_SerialConflict/` | **M5**：`Serial4.begin()` 之后 `Serial_Test` 还能不能收 | `tools\run-m5.ps1`（自己编译、烧写、发字节、验回显） |
| `sdram/SDRAM_Acceptance/` | **SD1**：`OpenPLC_SDRAM` 封装的 19 条断言 + 清零速率测量 | `tools\run-sdram.ps1` |

### SD1 · SDRAM 封装（需求 E5）

sketch 打 `RESULT <名字> PASS|FAIL` 和 `MEASURE <名字> <数>`，脚本按行判。**任何 FAIL、缺 `DONE`（说明跑一半挂了）、或一条 RESULT 都没有，都算失败。**

最关键的一条是 **`alloc_is_zeroed`** —— 封装存在的全部理由就是把"用户自己 memset"这个义务收进库里。另外 `alloc_before_begin_returns_null` 是"四个链接脚本坑不存在了"的直接证据：**拿不到地址就碰不到没初始化的 SDRAM。**

2026-08-17 实测：`begin()` 1.4 ms，清零 **91 MB/s**（清满 64MB ≈ 701 ms），`allocUninitialized()` 0 µs。

### M5 · 诊断串口不被用户 sketch 掐掉（需求 E7）

| 判据 | 前置条件 |
|---|---|
| sketch 调过 `Serial4.begin()` 之后，往端子发 5 个字节，`Serial_Test` **全部回显** | 板子在线、COM5 接在端子上、`$ARDUINO_CLI` 已配 |

⚠️ **判据是"能收"不是"能打印"。** 修之前的故障模式恰恰是输出先看着正常。

⚠️ **必须用 `Serial4`，不能用 `Serial`。** 当前 FQBN 是 `usb=CDCgen`，`Serial` 是 USB CDC，**碰不到 UART4** —— 拿 `Serial` 写的用例在**坏 core 上也会通过**。这个坑 2026-08-17 踩过：第一版用例在未修的 core 上跑出了干净的通过。

⚠️ **在未修的 core 上触发这个缺陷会让板子失联**（app 挂死、UDP 不应答、IAP 够不着）。恢复：

```powershell
STM32_Programmer_CLI -c port=SWD mode=UR -e 1   # 擦掉 app 扇区，bootloader 会停下并起以太网
```

然后正常 IAP 烧一次。**没有 ST-Link 的时候不要去复现它。**

⚠️ 端子 C05/C06 是**真 RS-232 电平（±12V）**，接 TTL 适配器可能烧掉适配器。

以后的 `rs485/` `can/` `knx/` 按同样方式各自一个目录，每个目录一份说明文件写清"验证什么 / 前置条件 / 判据"，名字取成 `RS485-ECHO.md` 这种能看出内容的。

## 未覆盖

**完整的覆盖矩阵和每条待补用例的设计骨架在 [docs/handover/TEST-PLAN.md](../../open_plc_cube_ide/docs/handover/TEST-PLAN.md)**，这里只留摘要：

| ID | 内容 | 为什么还没做 |
|---|---|---|
| M3 | 两块板子的 MAC 不同 | ⛔ 手上只有一块板。**唯一的硬阻塞** |
| S4a/S4b | 掉电中断，拆成传输中 / 擦写中两半 | 要人工断电 |

**2026-08-17 已补上：** ~~S2~~（密钥不匹配）、~~S3~~（启动期签名失败）、~~AU1~~（nonce 跨掉电）、~~DG1~~（降级拦截，不需要板子）。

### ⚠️ `IAPTool` 退出 ≠ 升级完成

**写任何驱动烧写的自动化之前先读这条。** IAPTool 送完最后一个字节就打 `File transfer complete.` 并退出 —— 板子**此时才开始**校验、擦除 app 区、从 SDRAM 往 flash 写，要好几秒。那几秒里复位或断电会毁掉 app。

判断升级真的完成，**要等板子自己说** `Checksum and signature OK. Rebooting...`。

2026-08-17 就是这么踩到的：`run-s3.ps1` 在 IAPTool 退出后立刻 ST-Link 复位，正好落在擦写中间，把 app 毁了，然后报"恢复镜像起不来" —— 排查方向差点跑偏到固件上。

**顺带**：这也说明 **S4b 的窗口比想象中好命中**，等 IAPTool 一退出就动手即可，不用掐秒表。

~~G1 上传失败后旧 app 仍可启动~~ —— **2026-08-17 已实测通过**，见下。

S1 和 S2（密钥不匹配）必须分开测：两者现象都是"拒绝启动"，混在一起测过一次，导致把密钥轮换误判成校验代码的 bug。

### SDRAM staging 改变了失败语义（2026-08-17 已实测）

镜像先进 SDRAM 校验，**全部通过之后才擦 app 区**（见 `open_plc_cube_ide/docs/IAP-STATUS.md`）。于是：

| | 改前 | 改后（**已实测**） |
|---|---|---|
| S1 跑完 | 板子拒绝启动 app，必须重烧才能恢复 → 标了 `destructive` | **app 区没被碰过，旧 app 照常启动** → ✅ `destructive` 已改为 `false` |
| S4 的做法 | 写到 49152 字节时拔电 | 那个时刻 flash 压根没被碰过，**用例失去意义**，仍待重写 |

**G1 的实测记录**（`tools/run-case.ps1 -Case S1 -ThenReset`）：

```
Transfer complete, verifying the staged image...
Signature verification FAILED - firmware not trusted. Application region untouched.
  ↓ 复位
** APP Mod ...
[BOOT] millis=12 / UART echo ready
```

还没做的：

1. **G1 收进 TestTool** —— 现在靠 `tools/run-case.ps1 -ThenReset` 跑，判据是复位后出现 `APP Mod`。要变成正式用例得让 TestTool 自己能复位板子（现在复位是 ST-Link 做的）
2. **S4 拆两半** —— 传输中拔电（旧 app 应照常启动）／擦写阶段拔电（应报 app 无效且可重传）。后者窗口只剩几秒，不好命中，**都要人工断电配合**
