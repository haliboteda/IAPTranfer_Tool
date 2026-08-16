# TestTool

**这个产品的测试与验收总入口。** 和 IAPTool 分开：**IAPTool 只负责上传烧写**，所有为了验证设备行为而存在的东西放在这里。

需要"传输进行中"的用例不会自己实现传输，而是**把 IAPTool 当子进程拉起来**跑真实烧写 —— 被测的始终是出货代码路径，不是测试代码里的仿制品。

## 目录结构

```
TestTool/
├── *.go                  ← 设备行为用例（T/S/N 系列），package main
├── config/
│   ├── machine.ps1       ← 本机路径。换电脑只改这一个文件（gitignore）
│   └── machine.example.ps1
├── tools/                ← 自动化工具，本身不是测试
│   ├── _common.ps1       ← 共用：读 config、找工具链、开串口、判目标电压
│   ├── selfcheck.ps1     ← ★ 所有不需要板子的检查，一条命令
│   ├── check-version-sync.ps1  ← P1  版本号三处一致
│   ├── check-mirror-sync.ps1   ← P2  跨仓镜像 8 锚点 + 备份寄存器占用
│   ├── check-core-sync.ps1     ← P3  core live vs git 仓库
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

> **需求清单和完整的覆盖矩阵在 `open_plc_cube_ide/docs/ai/`** —— [REQUIREMENTS.md](../../open_plc_cube_ide/docs/ai/REQUIREMENTS.md) 说要做到什么，[TEST-PLAN.md](../../open_plc_cube_ide/docs/ai/TEST-PLAN.md) 说每条用例覆盖哪条需求、最近一次跑出什么结果、还欠哪些用例。
> **本文件只管判据和运行方法**（贴着代码走，跨仓不搬）。

⚠️ **机器相关的路径只允许出现在 `config/machine.ps1`。** 脚本里写死绝对路径、或用 `..\..\..\` 数上去，换台电脑或挪个目录就废 —— 这两种都犯过。

## 快速开始

```powershell
# 一次性：填本机路径
Copy-Item config\machine.example.ps1 config\machine.ps1   # 然后编辑

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
| **G1** | 被拒绝的上传**不破坏已装好的 app** | 紧接 S1 之后复位 | 下次启动出现 `** APP Mod ...`，**不是** `no valid application`。用 `tools/run-case.ps1 -Case S1 -ThenReset` 跑 |

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
| `host/fakeboard/` | `.\run-cases.ps1`，需要 python | IAPTool 在传输开始前的密钥匹配决策，六种情况。**每种在真板子上都要换一把 bootloader 密钥才能构造** |
| `host/crypto_ref/` | `.\run-checks.ps1`，需要 python | SHA-256 构造对 hashlib（309 向量）；IAPTool 真实签名交给一份独立的纯算术 P-256 验证器 |

每个目录有自己的 README，写清"为什么这条不能在板子上测"。

`tools/` 下还有三个纯静态检查，不碰任何代码执行：`check-version-sync.ps1`（版本号三处一致）、`check-mirror-sync.ps1`（跨仓镜像 8 个锚点 + RTC 备份寄存器占用）、`check-core-sync.ps1`（core live 与 git 仓库）。它们对应发版检查单的 B1 / B2 / B3，以前是人工核对。

> **`host/iapcrypto/` 之前是独立 Go module，待在 `Hardware/TestCase/` 下，靠 `replace IAPTool => ../../../IAPTranfer_Tool` 相对路径挂过来。**
> 结果是 `go test ./...` 永远扫不到它 —— 2026-08-16 并入本 module 时才发现它**早就编译不过**（`iapcrypto.FixedPassword` 在密码改成运行时加载后就没了）。
> **教训：测试放在主构建扫不到的地方，等于没有测试。**

## 板上测试（`onboard/`）

| 目录 | 是什么 | 怎么用 |
|---|---|---|
| `rs232/SerialPort/` | UART + USB-CDC 回显 sketch | 用 Arduino IDE/CLI 编译上传，往端子 C05/C06 发字符看回显 |

⚠️ 端子 C05/C06 是**真 RS-232 电平（±12V）**，接 TTL 适配器可能烧掉适配器。

以后的 `rs485/` `can/` `knx/` 按同样方式各自一个目录，每个目录一份 README 写清"验证什么 / 前置条件 / 判据"。

## 未覆盖

**完整的覆盖矩阵和每条待补用例的设计骨架在 [docs/ai/TEST-PLAN.md](../../open_plc_cube_ide/docs/ai/TEST-PLAN.md)**，这里只留摘要：

| ID | 内容 | 为什么还没做 |
|---|---|---|
| M3 | 两块板子的 MAC 不同 | ⛔ 手上只有一块板。**唯一的硬阻塞** |
| S2 | 密钥不匹配（和 S1 分开） | 待做，成本低 |
| S3 | 故意损坏已烧录的 app → 纯启动期签名失败 | 待做，需 ST-Link |
| S4a/S4b | 掉电中断，拆成传输中 / 擦写中两半 | 要人工断电；S4b 窗口只剩几秒 |
| AU1 | 板上 nonce 跨掉电不重复 | 待做。**C5 目前完全没有覆盖** |
| DG1 | 降级被拦下 | 待做。**不需要板子**，fakeboard 加两个用例即可 |

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
