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
│   ├── common.py         ← 同上，Python 侧。`python tools/common.py --probe` = ENV
│   ├── selfcheck.py      ← ★ 所有不需要板子的检查，一条命令（15 步）
│   ├── selfcheck.ps1     ← 同上，M7 的对照基准，第 6 步删
│   ├── check-version-sync.ps1  ← P1  版本号三处一致
│   ├── check-mirror-sync.ps1   ← P2  跨仓镜像 9 锚点 + 备份寄存器占用
│   ├── check-core-sync.ps1     ← P3  core live vs git 仓库
│   ├── check-public-root.ps1   ← P6  公开根指纹没漂移
│   ├── check_*.py              ← 上面四个的 Python 版（M7 第 2 步）
│   ├── check_status_sync.py    ← P7  总表和用例名单不得漂
│   ├── check_doc_dupes.py      ← P8  同一句话不得出现在两个文件
│   ├── check_doc_paths.py      ← P9  文档里提到的路径必须存在
│   ├── check_allow_hygiene.py  ← P10 本机 allow 列表不许攒字面命令（建议性，不进 selfcheck）
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

> **需求、覆盖矩阵和最近结果在一张表里：[open_plc_cube_ide/docs/STATUS.md](../../open_plc_cube_ide/docs/STATUS.md)** —— 要做到什么、每条用例覆盖哪条需求、跑出什么结果、还欠哪些用例。（2026-08-22 之前那是分开的 REQUIREMENTS.md 和 TEST-PLAN.md，两份都已不存在。）实测数字的唯一出处是 [MEASUREMENTS.md](../../open_plc_cube_ide/docs/test/MEASUREMENTS.md)。
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

⚠️ **`--prereqs` 和 ENV 的分工**：`--prereqs` 看 PATH 上的运行时（git / Python / Go / cc / PowerShell / pyserial）在不在，selfcheck 的 ENV 一步看本机路径解析成了什么。前者必须在 Python 里，因为 **ENV 要 PowerShell 才跑得起来，而 pwsh 恰好是 Debian / macOS 上最可能缺的那一个**。

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
python tools\selfcheck.py         # 15 项；--list 先看它跑哪几步；--quick 跳过慢的那 5 项

# 构建 bootloader、烧写、抓启动日志、给判定（CubeIDE 必须关闭）
.\tools\flash-bootloader.ps1

# 只看串口，不碰板子
.\tools\serial-watch.ps1
```

⚠️ **`selfcheck` 用 Python 那一版。** `selfcheck.ps1` 还在，但只作为 M7 的对照基准留到第 6 步；两版**15 项结论逐项相同**（judged by `tools/m7-compare.ps1 -Only selfcheck`）。**板级脚本目前仍然只有 PowerShell 版**（M7 第 5 步还没做），所以上面后两条还是 `.ps1`。

缺什么会报 `SKIP` 并说清缺什么，**不会静默跳过** —— 一个被悄悄跳过的检查会被读成通过，那比没有这个检查更糟。

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

**这一层每个脚本都有 PowerShell 和 Python 两个版本**（M7 第 3 步，2026-08-22）。两版在 Windows 上**输出逐字节相同**，判据由 `tools/m7-compare.ps1` 保证（10/10）。新写自动化用 Python 那一版；`.ps1` 留着当对照基准，到 M7 第 6 步才删。

| 目录 | 怎么跑 | 覆盖什么 |
|---|---|---|
| `host/iapcrypto/` | 在 `IAPTranfer_Tool/` 下 `go test ./TestTool/...` | HMAC 原语对 RFC 4231 向量；派生公式 `HMAC-SHA256(password, machineID)`；同 UID 稳定、异 UID 必不同；一次完整挑战应答双方独立算出同一个 HMAC |
| `host/bootloader_unit/` | `python build.py`（或 `.\build.ps1` / `./build.sh`），需要 gcc/clang | 用 stub 在主机上编译**真实的** `sha256.c` / `iap_keyderive.c` / `iap_auth.c` 并跑断言 |
| `host/fakeboard/` | `python run_cases.py`（或 `.\run-cases.ps1`） | **K1–K6** IAPTool 在传输开始前的密钥匹配决策，六种情况。**每种在真板子上都要换一把 bootloader 密钥才能构造** |
| `host/fakeboard/` | `python run_downgrade.py`（或 `.\run-downgrade.ps1`） | **DG1** 降级拦截，五种情况。每条都额外断言**板子有没有真的收到 `flash` 命令** —— 只看工具打了什么，挡不住"打印了拒绝然后照样上传"。⚠️ **只覆盖工具侧**：bootloader 把版本号解析出来却从不比较（`IAPServer/IAP_server.c:332-357`），设备侧那半是 DG2 |
| `host/crypto_ref/` | `python run_checks.py [--rounds N]`（或 `.\run-checks.ps1 -Rounds N`） | SHA-256 构造对 hashlib（309 向量）；IAPTool 真实签名交给一份独立的纯算术 P-256 验证器 |
| `host/variant_check/` | `python build.py`（或 `.\build.ps1`），需要 arduino-cli | **P4** Arduino 变体头的编译期断言。目前一个：FMC 保留脚表（39 个）自洽。**编不过就是变体头坏了，不是 sketch 坏了** |
| `host/examples_build/` | `python build.py [--only LIB]`（或 `.\build.ps1 -Only LIB`），需要 arduino-cli | **P5** 编译 core 自有库的**每一个 example**。⚠️ **约十分钟，故意不进 selfcheck** —— 见下 |

⚠️ **两个 fakeboard 脚本一次只能有一个在跑。** 它们都在同一个端口起假板子，而 `fake_board.py` 的 TCP socket 设了 `SO_REUSEADDR` —— 两个同时跑不会报错，只会有两个进程同时监听、谁接到连接是未定义的，表现为莫名其妙的 FAIL。

## ⚠️ 板级用例的顺序约束和标准载荷（2026-08-22 实测定的）

**`SD1` 必须是任何序列里最后一条依赖网络的用例。** 它装上的 `SDRAM_Acceptance` app **会让板子对 IAPTool 不可达** —— 回 ICMP，但不回 UDP 发现，`IAPTool ether` 报 `No response`。它后面每一条走网络的用例都会以「板子没反应」失败，而原因和那些用例毫无关系。A/B 三态实测见 `open_plc_cube_ide/docs/test/MEASUREMENTS.md`；根因待查，见那个仓库 `docs/work/ISSUES.md` 的 **B5**。

**所以板级用例的标准载荷是 `onboard/rs232/SerialPort`，不是 `SDRAM_Acceptance`。** 需要 `--bin` 的用例（S1 / S2 / T3 / S3 / upload-and-watch）都用它 —— 实测装上它之后发现应答在 **0.1 秒**内回来。

**被 SD1 弄成不可达之后怎么恢复**（三条都行，前两条不需要人动手）：ST-Link 复位进不去 —— app 是有效的，会照常启动。要么 `flash_bootloader.py` 整片擦除后重烧，要么走 CDC（`COM11`），要么按住 BOOT0。

### P5 · example 不能腐烂

**什么时候跑**：改了 `open_plc_arduino` 的任何库之后，以及发版前。**不在 `selfcheck` 里** —— selfcheck 是"改完代码就跑"的东西，往里加十分钟只会让人不跑它。

```
python host/examples_build/build.py              # 全部
python host/examples_build/build.py --only SDRAM  # 只挑一个库
```

⚠️ **`m7-compare.ps1` 只比对 `--only SDRAM` 的子集。** 全量跑两遍要二十分钟，而对脚本逻辑不增加任何覆盖 —— 一个库的例子已经走完除「例子更多」以外的每个分支。**全量仍然要手工跑**，时机就是上面那句。

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

### P1 · 版本号三处一致

```
python tools/check_version_sync.py       # 或 .\tools\check-version-sync.ps1
```

比对 bootloader（`Core/Inc/IAP_config.h` 的 `OPENPLC_FW_VERSION`）、Arduino core（`boards.txt` 的 `build.fw_version`）、`RELEASE-NOTES.md` 最新的版本标题——三处本来毫无关联，各改各的。曾经真漂过：bootloader 停在 0.1.2、core 已经是 0.1.3，同一块板子报出两个版本号，像一次失败的升级。对应发版检查单 **CHK-B1**。退出码：0 三处一致，1 有分叉，2 缺文件。

### P2 · 跨仓镜像没分叉

```
python tools/check_mirror_sync.py        # 或 .\tools\check-mirror-sync.ps1
```

`open_plc_cube_ide/docs/design/ARCHITECTURE.md` 列出的跨仓镜像代码，三个仓库没有共享构建系统，一侧改了另一侧不会报错，只会在运行时表现成不相关的症状。比的不是整份文件（C++ 侧有 `extern "C"`，两边 API 也不一样），是**每一项一个语义锚点**——只要求锚点一致。**没被检查覆盖的锚点会在输出末尾点名列出**，全绿不代表全覆盖。对应 **CHK-B2**。退出码：0 全部锚点一致，1 至少一处分叉，2 缺文件。

### P3 · core live 与 git 仓库一致

```
python tools/check_core_sync.py          # 或 .\tools\check-core-sync.ps1
```

比对 Arduino IDE **真正加载**的那份（`$CORE_LIVE`）和本仓库 git 版（`$CORE_REPO`）。方向天生单向：改动在 `$CORE_LIVE` 里做、验证、再拷回仓库提交——`$CORE_LIVE` 不进版本控制，验证过忘了拷回来，那段代码就只活在这台机器上，重装一次 IDE 就没了。六类刻意排除在比对之外：IDE 自己的安装元数据、Go 构建产物、编辑器备份、`.claude/`、`.vscode/`（这两个是编辑器往任何打开的目录里写的，2026-08-21 就因为 CMake 插件写了一条本机绝对路径而误报过一次）。对应 **CHK-B3**。退出码：0 一致，1 有差异，2 仓库路径不对。

### P7 · 总表和用例名单不得漂

```
python tools/check_status_sync.py        # 或加 --list 只打印解析结果
```

`docs/STATUS.md` 和 `TEST-CASES.md` 里的用例编号必须是同一个集合。抓三类漏洞：STATUS.md 拿某条用例当证据、但 TEST-CASES.md 没定义它（需求指着一条谁都跑不了的用例）；TEST-CASES.md 定义了某条用例、但没有需求在引用它（一条跑出来的结果没人记录，烂了也没人发现）；某条用例引用的需求号 STATUS.md 里不存在。**抓不到的**：状态本身过期（"BG1 昨天就失败了但 D4 还写 PASS"）——那需要跑分结果自己传回表里，今天还是人工填的，`docs/test/COVERAGE-GAPS.md` 的"证据是否仍然有效"那一列就是留着补这个洞。退出码：0 两边一致，1 有漂移，2 缺文件。

### P8 · 一个事实只能写在一个文件里

```
python tools/check_doc_dupes.py          # 加 --min 40 只看更长的断言；--code 连代码块也列
```

把每份文档切成句子，去掉 markdown 加粗之类的强调符号（这样加粗过的一份能跟没加粗的一份对上），任何长到能算"断言"的句子出现在两个以上文件里就判失败。**指针（"见 X"）不算**——指针短且泛化，这正是修复重复的手段本身。**代码块单独报告、不计入失败**：抓下来的日志、命令这类东西合理地要在多处原样出现（发布说明要给客户看到他会看到的确切字符串，验收记录要写板子实际打了什么）——它们引的是那份 `.c` 文件，不是互相抄。2026-08-22 那次重组扫出十九段重复散文、三个因此漂开的数字。退出码：0 没有断言重复，1 至少一处，2 环境问题。

### P9 · 文档里提到的路径必须存在

```
python tools/check_doc_paths.py          # 加 --list 打印它 resolve 出的每条路径
```

只检查三种能明确判断"相对谁"的写法：markdown 链接（相对当前文档）、`$BOOT`/`$TOOL`/`$CORE` 这类仓库变量路径、反引号包住的 `docs/`开头的路径（相对某个仓库根）。**故意不检查其余所有反引号路径**——一条不带仓库变量的裸路径意思是"相对这段话在讲哪个仓库"，检查脚本猜不出来；第一版试过猜，结果报出 154 处死链接、真的只有五处，一个哭喊了 150 次的检查比没有检查更糟。想让某条裸路径也被查到，就给它加上仓库变量前缀。路径里的行号（如 `fmc.c` 后面跟的行号范围）在检查前会被去掉——文件必须存在，行号只是提示，本来就会漂。2026-08-22 那次文档重组当场弄断了 105 处引用，全靠手工找出来，这条检查就是为了不再靠人眼。退出码：0 每条路径都能 resolve，1 至少一条断链，2 环境问题。

### P10 · allow 列表不许攒字面命令

```
python tools/check_allow_hygiene.py                  # 每个仓库一行汇报
python tools/check_allow_hygiene.py --list            # 连被点名的条目也打出来
python tools/check_allow_hygiene.py --fail-over 400   # 超过这个数才算失败
```

`.claude/settings.local.json` 每次人批准一条"以后别再问"，就原样追加一行——没有任何东西会删。攒到某个点，`allow` 数组里全是"当时那条绝对路径 + 当时那个 `-First 45`"这种再也不会命中第二次的记录，而真正该有通用模式的仓库反而一条没有，每条命令都弹窗。2026-08-23 实测过一次：一个仓库攒到 481 条，其中 426 条是这种一次性记录；同期跑全部测试的那个仓库是 0 条。

**这条不是发版门禁**——`settings.local.json` 本机专属、不进 git，不同机器天然不同，没法当"必须全绿"的检查。默认只打印、退出码 0；只有 `--fail-over` 指定阈值且真的超了才返回 1。**判据只看"像不像一次性"**（是否带绝对路径、是否带 `-First N` 这种烤进去的输出切片），会把一些合理的本机安装路径也点出来（比如某个工具链装在哪，本来就该按机器记）——这是故意的宽松，宁可多提醒，不做成"哭喊 150 次"的那种检查。

### M7 · PowerShell 版和 Python 版必须给出同一个结论

**只在 M7 改写期间存在**（见 `open_plc_cube_ide/docs/work/M7-python-scripts.md`）。两个脚本，都不碰板子，随时可跑：

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

### SD2 · SDRAM 数据总线诊断（不是通过/失败用例，是仪器）

`tools\run-sdram-diag.ps1` —— 发一条串口命令、抓 bootloader 打出来的四张表：逐位统计、驻留扫描、释放时间对比、浮空测试。实现在 `boot:Core/Src/sdram_diag.c`，命令是 `sdramdiag` 和 `sdramlive [秒]`。

**为什么要有它**：这块板的 SDRAM 数据总线**一个可测点都没有** —— 16 根线两端都在 BGA 球下，没有串阻也没有测试点（`boot:docs/design/HARDWARE-FACTS.md`）。万用表和示波器没有落点，固件是唯一的仪器。

| 想干什么 | 命令 |
|---|---|
| 完整报告 | `.\run-sdram-diag.ps1 -Out run2.txt` |
| 冷热喷剂测试（每秒一行错误率） | `.\run-sdram-diag.ps1 -Live 120` |

**这里没有 PASS/FAIL 判据**，因为它输出的是测量值不是结论。判读规则和已经排除的解释在 `boot:docs/test/MEASUREMENTS.md`，给硬件工程师看的一页在 `boot:docs/work/investigations/sdram-d1-report.html`。

⚠️ **前置条件：板子必须停在 bootloader。** 跑起 app 的板子根本不初始化 FMC，什么都测不到。

⚠️ **释放时间只能在同一个 GPIO 端口内比较。** 好线之间跨 2.2 倍很正常（走线长度差别），跨端口比出来的数没有意义。

⚠️ **capture 存进 `host/sdram_diag/`，一次一个文件，不要覆盖** —— 那是某一块板在某一刻的物理状态，修过或换过就再也测不到了。

## 未覆盖

**完整的覆盖矩阵和每条待补用例的设计骨架在 [docs/STATUS.md](../../open_plc_cube_ide/docs/STATUS.md)**，这里只留摘要：

| ID | 内容 | 为什么还没做 |
|---|---|---|
| M3 | 两块板子的 MAC 不同 | ⛔ 手上只有一块板 |
| S4a/S4b | 掉电中断，拆成传输中 / 擦写中两半 | 要人工断电。**脚本已就绪：`tools/run_s4.py`**。⛔ **2026-08-21 起被硬件阻塞** —— SDRAM 的 D1 线开路，任何上传都停在 checksum，见 `open_plc_cube_ide/docs/work/ISSUES.md` 的 B4 |

### `tools/run_s4.py` —— S4a / S4b 怎么跑

```bash
python3 tools/run_s4.py --case a --bin <app.bin> --pad-to 1200000
python3 tools/run_s4.py --case b --bin <app.bin> --retry 3
```

| | |
|---|---|
| **判据 S4a** | 传输窗口内断电 → 重新上电后**旧 app 照常启动**（日志出现 `APP Mod`，且**没有** `App signature invalid or absent`）|
| **判据 S4b** | 擦写窗口内断电 → 上电报 `App signature invalid or absent`，**且重传一次能恢复** |
| **窗口锚点** | `Staging in SDRAM` 之后 / `Erasing application region` 之前 = S4a；`Erasing application region` 之后 = S4b。字符串对齐 `open_plc_cube_ide/IAPServer/IAP_server.c` |
| **不按回车** | 照 `run-au1.ps1` 的先例：脚本读板子自己的日志判断在哪个窗口，**用 ST-Link 实测目标电压证明电真的掉了** —— 串口静默和 UDP 不应答在"正忙着擦除"时也会发生，只有电压不会骗人 |
| **⚠️ ST-Link 可能在供电** | STLINK-V3 能输出 3.3V。如果它在供电，拔板子的电等于没拔，两条用例都是空测。脚本发现电压没掉会**拒绝记成通过** |
| **为什么要 `--pad-to`** | 真实 app 只有 83 KB，以太网传输不到一秒，S4a 的窗口手动追不上。补零到 ~1.2 MB 让窗口有几秒。尾部补零不影响启动（向量表和代码在头部），所以万一窗口没追上、镜像真被写进去，板子拿到的仍是能跑的 app |
| **窗口没追上** | 脚本自己认得出（看到了本该在断电后才出现的下一条日志），报 `missed` 并可 `--retry`，**不会把落在别处的断电记成通过** |

**2026-08-17 已补上：** ~~S2~~（密钥不匹配）、~~S3~~（启动期签名失败）、~~AU1~~（nonce 跨掉电）、~~DG1~~（降级拦截，不需要板子）。

### ⚠️ `IAPTool` 退出 ≠ 升级完成

**写任何驱动烧写的自动化之前先读这条。** IAPTool 送完最后一个字节就打 `File transfer complete.` 并退出 —— 板子**此时才开始**校验、擦除 app 区、从 SDRAM 往 flash 写，要好几秒。那几秒里复位或断电会毁掉 app。

判断升级真的完成，**要等板子自己说** `Checksum and signature OK. Rebooting...`。

2026-08-17 就是这么踩到的：`run-s3.ps1` 在 IAPTool 退出后立刻 ST-Link 复位，正好落在擦写中间，把 app 毁了，然后报"恢复镜像起不来" —— 排查方向差点跑偏到固件上。

**顺带**：这也说明 **S4b 的窗口比想象中好命中**，等 IAPTool 一退出就动手即可，不用掐秒表。

~~G1 上传失败后旧 app 仍可启动~~ —— **2026-08-17 已实测通过**，见下。

S1 和 S2（密钥不匹配）必须分开测：两者现象都是"拒绝启动"，混在一起测过一次，导致把密钥轮换误判成校验代码的 bug。

### SDRAM staging 改变了失败语义（2026-08-17 已实测）

镜像先进 SDRAM 校验，**全部通过之后才擦 app 区**（见 `open_plc_cube_ide/docs/test/MEASUREMENTS.md`）。于是：

| | 改前 | 改后（**已实测**） |
|---|---|---|
| S1 跑完 | 板子拒绝启动 app，必须重烧才能恢复 → 标了 `destructive` | **app 区没被碰过，旧 app 照常启动** → ✅ `destructive` 已改为 `false` |
| 原来那个 S4 | 写到 49152 字节时拔电 | 那个时刻 flash 压根没被碰过，**用例失去意义**。✅ 已拆成 **S4a**（传输中拔电 → 旧 app 照常启动）和 **S4b**（擦写阶段拔电 → 报无效且可重传），骨架在 `open_plc_cube_ide/docs/test/CASE-DESIGNS.md`，跑法 `tools/run_s4.py` |

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
