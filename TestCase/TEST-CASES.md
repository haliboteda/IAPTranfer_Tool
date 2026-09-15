# TestCase

**这个产品的测试与验收总入口。** 和 IAPTool 分开：**IAPTool 只负责上传烧写**，所有为了验证设备行为而存在的东西放在这里。

需要"传输进行中"的用例不会自己实现传输，而是**把 IAPTool 当子进程拉起来**跑真实烧写 —— 被测的始终是出货代码路径，不是测试代码里的仿制品。

## 目录结构

```
TestCase/
├── *.go                  ← 设备行为用例（T/S/N 系列），package main
├── config/
│   └── machine.py        ← 本机路径（gitignore）。**生成的，不要手抄**
├── requirements.txt      ← 唯一的 pip 依赖：pyserial
├── tools/                ← 自动化工具，本身不是测试
│   ├── init_machine.py   ← ★ 本机路径生成器。先探测，搜不到才问你。`CORE_LIVE` 每次发版要重跑
│   ├── test_init_machine.py ← init_machine 提问逻辑的单元测试
│   ├── common.py         ← ★ 共用件：读 config、找工具链、开串口、跑子进程时排空串口、动手提示。`--probe` = ENV
│   ├── selfcheck.py      ← ★ 所有不需要板子的检查，一条命令
│   ├── check_version_sync.py  ← P1  版本号三处一致
│   ├── check_mirror_sync.py   ← P2  跨仓镜像 12 锚点 + 备份寄存器占用
│   ├── check_core_sync.py     ← P3  core live vs git 仓库
│   ├── check_public_root.py   ← P6  公开根指纹没漂移
│   ├── check_status_sync.py    ← P7  总表和用例名单不得漂
│   ├── check_doc_dupes.py      ← P8  同一句话不得出现在两个文件
│   ├── check_doc_paths.py      ← P9  文档里提到的路径必须存在
│   ├── check_allow_hygiene.py  ← P10 本机 allow 列表不许攒字面命令（建议性，不进 selfcheck）
│   ├── flash_bootloader.py     ← 无头编译 + ST-Link 烧写 + BG1 判定
│   ├── enter_bootloader.py     ← 把板子请进 bootloader，不用碰板子
│   ├── serial_watch.py
│   ├── run_case.py             ← 跑一条 TestCase 用例，`--then-reset` 变成 G1
│   ├── run_s3.py  run_s4.py    ← S3 启动期验签 / S4 掉电中断
│   ├── run_au1.py              ← AU1 nonce 唯一性，绕一次真实掉电
│   ├── run_m5.py  run_sdram.py ← M5 串口冲突 / SD1 SDRAM 封装
│   ├── run_takeown.py  run_setowner.py  inject_owner_record.py  ← 所有权 OW1/OW2
│   ├── upload_and_watch.py     ← 走真实 IAPTool 上传并判 SDRAM 暂存行为
│   └── can_send.py  can_watch.py  rs485_echo.py  ← 板级端口
├── host/                 ← 不需要板子，纯主机跑
│   ├── iapcert/          ← H1  证书签发、serial 计数器、挑战签名的 Go 单元测试
│   ├── bootloader_unit/  ← H2  用 stub 编译真实 bootloader 源码的 C 单元测试
│   ├── porttool_caps/    ← H4  端口工装协议契约：C harness 跑真实固件源码产出
│   │                          caps_golden.txt，Go 测试再拿它验 internal/ptproto
│   ├── porttool_plan/    ← H1  方案文件、判据算子、执行器、报告，以及方案页的
│   │                          HTTP 面（板子由脚本假扮，逐条命令自己决定怎么答）
│   ├── fakeboard/        ← K1–K7  IAPTool 传输前的密钥/证书匹配决策，七种情况
│   └── crypto_ref/       ← X1/X2  SHA-256 与 ECDSA 的独立实现交叉验证
├── onboard/              ← 需要烧到板子上跑
│   └── rs232/SerialPort/ ← O1  UART + CDC 回显 sketch
└── acceptance/
    └── checklist.md      ← 出厂 / 量产验收单
```

> **需求、覆盖矩阵和最近结果在一张表里：[open_plc_cube_ide/docs/STATUS.md]($PROD/docs/STATUS.md)** —— 要做到什么、每条用例覆盖哪条需求、跑出什么结果、还欠哪些用例。
> **本文件只管判据和运行方法**（贴着代码走，跨仓不搬）。

⚠️ **机器相关的路径只允许出现在 `config/machine.py`。** 脚本里写死绝对路径、或用 `..\..\..\` 数上去，换台电脑或挪个目录就废 —— 这两种都犯过。

⚠️ **`init_machine.py` 有单元测试**：`python tools/test_init_machine.py`（**32 个用例**，四组）。

| 组 | 测什么 | 为什么只能这么测 |
|---|---|---|
| tables | `SETTINGS` / `PREREQS` / `EXAMPLES` 的列是否齐 | 三张手写表，最常见的编辑错误是加了一行漏一列；不测的话报错发生在你不在场的那台机器上 |
| ports | `detect_log_ports()` 的 **macOS 分支** | 本项目没有 mac，所以文件系统是假的，只测排序与去重逻辑 |
| claude dirs | `--write-claude-dirs` 的合并、`ignored_by_own_rules()` 的护栏 | 它改的那个文件装着几百条手工批准的权限规则，**"没弄丢东西"就是被测的性质** |
| ask_for | 引号剥离、`~` 展开、安装根目录校验 | 提问按定义在自动化里跑不到（只在"stdin 是终端且环境无自动化标记"时发生），只能替换 `input()` |

退出码：0 全过，1 有失败，2 全过但 ask_for 那组因这台机器没有真 CubeIDE / Arduino IDE 而跳过 —— **不假装通过**。

⚠️ **那两个文件是 `tools/init_machine.py` 生成的，不要手写、也没有模板可抄。** 需要一个新的本机路径时，把它连同探测方式加进那个脚本的 `SETTINGS` 表 —— 那里是"这台机器有什么"的唯一记录。以前的 `machine.example.*` 已删除：它和 `SETTINGS` 是同一份清单的两个出处，留着必然漂移。

## 快速开始

```
# 手工的话：

# 一次性：探测本机路径，生成 config/machine.py
python tools\init_machine.py      # Linux 上是 python3

# 让一个仓库里的 Claude 会话读得到兄弟仓库，不必一路批权限
python tools\init_machine.py --write-claude-dirs

# 所有不需要板子的检查。改完代码先跑这个，全绿了再考虑上板
python tools\selfcheck.py         # 15 项；--list 先看它跑哪几步；--quick 跳过慢的那 5 项

# 构建 bootloader、烧写、抓启动日志、给判定（CubeIDE 必须关闭）
python tools/flash_bootloader.py

# 只看串口，不碰板子
python tools/serial_watch.py
```

⚠️ **测试脚本一律用 Python**（2026-09-01 定）。

缺什么会报 `SKIP` 并说清缺什么，**不会静默跳过** —— 一个被悄悄跳过的检查会被读成通过，那比没有这个检查更糟。

## 编译

```sh
# 在 IAPTranfer_Tool/ 下
go build -o Output/windows/TestCase.exe ./TestCase
```

和 IAPTool 共用一个 Go module，不引入额外依赖，也不会让 `IAPTool.exe` 变大。

## 运行

```sh
TestCase <case-id|all> --ip=<addr> [--port=56865] [--bin=<file.bin>] [--iaptool=<path>]
         [--key=<owner.pem>]
```

- `--ip` 必填。设备 IP 从串口日志的 `[NET]` 行读，或用 `IAPTool ether` 的广播发现看。
- `--bin` T3 / S1 / S2 需要；`--key` S1 / S2 需要，是板子当前信任的那把私钥。
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

### UDP 发现（`udp_discovery.go`）

每一次以太网升级都从一条发现回复开始，所以发现一旦不稳，现场看到的是"板子不见了"。

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **N1** | 四个关键词都应答 | 设备在线 | `openplc_server_where_r_y` / `DISCOVER` / `openplc_discover` / `ping` 全部有回复 |
| **N2** | 多轮间隔查询都应答 | 设备在线 | 6 轮全部有回复 |
| **N3** | 回复落在工具的超时之内 | 设备在线 | 20 轮全部在 2s（IAPTool 的 `CommandTimeout`）内返回 |
| **N4** | 长时间浸泡下发现依然可靠 | 设备在线，`--minutes=N`（默认 10） | 整段时间内零次无应答 |
| **N5** | 泛洪被封顶，且封顶不会把发现打死 | 设备在线 | 以约 500 次/秒猛打 3 秒，回复速率不超过 50/s 的上限；**且随后正常查询仍能应答** |

### 签名校验（`signature.go`）

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **S1** | 签名无效的镜像被拒绝 | 设备停在 bootloader，需 `--bin` 和 `--key`（板子信任的那把，用来签挑战） | 传完后设备回 `Signature Failed`（或 `No Signature`） |
| **S2** | 被**别的密钥**签过的镜像被拒绝 | 同 S1，另需 `--iaptool`（用它生成临时密钥并签名） | 同上。**外加**上传前 `getpubkey` 必须和临时密钥不同 |
| **S3** | **已装好的** app 被改坏 → 启动期拒绝 | 板上有能启动的 app、ST-Link、**一个已签名的恢复镜像** | `metadata present` + `App signature invalid or absent`，且**没有** `** APP Mod` |
| **G1** | 被拒绝的上传**不破坏已装好的 app** | 紧接 S1 之后复位 | 下次启动出现 `** APP Mod ...`，**不是** `no valid application`。用 `python tools/run_case.py --case S1 --then-reset` 跑 |

```
python tools/run_s3.py --bin <app.bin>       # 破坏 + 判定 + 自动恢复
```

### 所有权（`python tools/run_takeown.py`，需求 C10）

OW1 / OW2 的动作走出货工具（`IAPTool takeown` / `setowner`），判据向板子要（原始 TCP `getowner` / `getpubkey`）。`--bad-signature` 是例外：出货工具做不出坏签名，那条验的是板子的行为，所以在用例里手工拼记录。

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **OW1** | 认领把板子绑到一把新密钥上 | 板子停在 bootloader，**且这次启动按住过 BOOT0** | `takeown` 回 `OK`；`getpubkey` 返回新密钥；复位后仍然认得，公开根告警消失 |
| **OW1-neg** | BOOT0 没按时认领被拒 | 停在 bootloader，**没按 BOOT0**（用 `enter_bootloader.py` 进） | 回 `Refused`，`getpubkey` **一字节不变** |

| **OW2** | 换 owner：现任签名才算数 | 板子已被一把**你持有私钥**的密钥认领 | 正确签名 → `OK` 且 generation +1；坏签名 → `Refused` 且什么都没变 |
| **OW2-attack** | 无签名的高 generation 记录**夺不走**板子 | 同上 | 扫描器看得见那条记录，但 `getpubkey` 仍返回原主人 |
| **OW3** | 恢复出厂，然后能重新认领 | 板子已被认领，**有人在板子旁** | 按住 BOOT0 十秒 → `FACTORY RESET DONE` → 回落内置根、公开根告警回来 → 再 `takeown` 能成功 |

```
python tools/run_takeown.py --expect-refused          # 认领负向，不需要人
python tools/run_takeown.py                         # 认领正向，需要有人按住 BOOT0
python tools/run_setowner.py --current-key a.pem      # 换 owner，不需要人
python tools/run_setowner.py --current-key a.pem --bad-signature
python tools/inject_owner_record.py --key <hex> --also-unsigned 9   # 夺取攻击
```

### 认证与重放（`nonce_replay.go`）

| ID | 验证什么 | 前置条件 | 判据 |
|---|---|---|---|
| **AU1** | nonce 不重复，且**掉电后不从头开始** | 设备停在 bootloader；**要人工断电一次**；VBAT 电池在位 | 两阶段所有 nonce 互不相同；阶段内计数器恰好 +1；断电后的第一个计数器**严格大于**断电前最后一个 |

```
python tools/run_au1.py                 # 编排两个阶段，中间提示你拔电
python tools/run_au1.py --resume         # 阶段 1 已经跑过了，直接等断电
```

### 委托证书怎么在真板子上复现

K1–K7 用假板子覆盖了工具的判断，H2 用真实 bootloader 源码覆盖了板子的判断。**两者中间那段——真板子收下一张委托证书并据此执行固件——只能手工走一遍**，发版前值得跑：

```
# 管理员：用板子当前信任的那把根，给"同事"的公钥发一张证书
IAPTool pubkey <同事>/keys/fw_signing_key.pem          # 128 hex
IAPTool cert <那 128 hex> --key=<owner.pem> > <同事>/keys/fw_signing_key.pem.cert

# 同事：什么参数都不用加
IAPTool ether app.bin <ip>
```

判据三条，缺一不可：

1. 工具打出 `Certificate was issued by this board's root (<根前16位>...)` —— 不是 `Signing key matches this board`，那是自签路径
2. 上传成功，串口出现 `Checksum and signature OK`
3. **复位后进 `** APP Mod`** —— 证明启动期拿存下来的那张证书重验也过了，不只是上传时过了

反向：把证书换成另一把根签的，工具必须**在传输开始前**就拒，且报的是"这张证书不是这块板的根签的"。

## 怎么让设备停在 bootloader

T1–T4 和 S1 都要求设备处于 bootloader 且以太网已起。三种办法：

1. **`python tools/enter_bootloader.py`** —— 全自动，不需要碰板子。**推荐**
2. **按住 BOOT0 复位** —— 日志出现 `** UPLOAD Mod ... (BOOT0 held)`
3. 让 app 收到认证过的 UDP reboot（`IAPTool ether` 的第一步就是这个），但它随后会真的上传

## 主机侧测试（`host/`，不需要板子）

### 模拟板：手上没板子时怎么联调

**它是什么**：`TestCase/porttool/` 那些 `.c` 原样编成的 PC 程序，只换掉外设 stub 和最外层 main。命令解析、`pt.caps`、会话逻辑、帧格式、版本号全是固件那份源码，所以固件改了它编不过 —— **不会漂移**。设计理由见 `$BOOT/docs/design/DECISIONS.md` 第 29 条。

```bash
cd TestCase/host/porttool_caps && python build.py --sim   # 编，产出 harness/porttool_simboard.exe
porttool                                                  # 面板的端口列表里选 "sim"
porttool run --port sim --yes TestCase/plans/station6-poweron.json
cd TestCase/host/porttool_panel && python run.py --port sim   # H5，不用板子
```

**H5 旁边还有一个 `naive.py`，问的不是同一个问题。** `run.py` 知道每个控件在哪、
该点哪一个；`naive.py` 只认页面：进一个端口，把印在上面的按钮按印出来的顺序挨个
按一遍，读它自己那一段给出的结论，同时查这一段的排版（说明在不在、按钮是不是排在
配置后面结果前面、要人插的对端是不是排在所有用例之前）。三个 2026-09-14 的 bug
`run.py` 看不见，因为它按的是代码里的位置，不是屏幕上的位置。

```bash
cd TestCase/host/porttool_panel
python naive.py --port COM5 --peer rs485=COM16     # 真板子，17 个端口 27 个用例
python naive.py --port COM5 --only sd              # 只走一个口
python naive.py --port COM5 --long                 # 连全量的 SD 压力和 SDRAM 全片扫描一起跑
```

⚠️ **默认跳过 `sdram.sweep`（整片 64 MB）。** 那两项验的是
器件不是面板，而且占掉这个脚本大半的时间 —— 抽查就能回答「按下去给不给得出像样的
答复」。要给器件下结论，加 `--long`，或者自己在面板上点那两段。用户 2026-09-14 定。

⚠️ 它不点「持续」（那是几小时的老化），不走模拟输出那张多点测量卡（要人读万用表），
`--peer` 一次只说一个口，形如 `rs485=COM16` —— 面板分不出哪个适配器
插在哪个端子上，这个脚本也分不出。

**造故障看面板怎么显示**（手敲进它的 stdin，或在面板底部的命令框里）：

| 命令 | 干什么 |
|---|---|
| `sim.help` | 列全部 |
| `sim.din 0x00` | 数字输入全低，看 din 判失败 |
| `sim.vdda 1800` | 基准坏掉，落在方案的 2400–2600 之外 |
| `sim.ain <ch> <mv>` / `sim.temp <ch> <mv>` | 单路模拟量读数 |
| `sim.link 0` | 拔网线（PHY 的 BSR 和 netif 一起变） |
| `sim.walk 1` | 数字输入自己轮转，看面板动起来 |

⚠️ **`sim.` 开头的命令真板上一条都没有**，由 `sim_main.c` 自己拦下，不进固件的 `dispatch()`。

⚠️ **它证明不了任何硬件行为。** 读数全是 stub 造的一块理想板子，每根对端线都当接好的；UART 中断收发、`rx_errors`、真 ADC、真 PHY 都不在里面。**它验的是 PC 侧的线路和方案文件。**


跑得快、随时能跑，**改完代码先过这一层再上板**。

| 目录 | 怎么跑 | 覆盖什么 |
|---|---|---|
| `host/iapcert/` | 在 `IAPTranfer_Tool/` 下 `go test ./TestCase/...` | 证书布局与根签名覆盖的字节范围（换个范围就验错东西）；serial 计数器从 1 开始、递增、落文件；serial 小端落在偏移 64；挑战签名覆盖 `sha256(nonce\|\|msg)` 且顺序不可换 |
| `host/bootloader_unit/` | `python build.py`，需要 gcc/clang | 用 stub 在主机上编译**真实的** `sha256.c` / `iap_cert.c` / `fw_verify.c` / `iap_auth.c` 并跑断言。金标证书由出货工具生成，所以过了就等于 C 和 Go 对同一套线格式达成一致 |
| `host/porttool_caps/` | `python build.py`，需要 gcc/clang | **H4** 端口工装的协议契约，判据见 [PORTTOOL-CAPS-TEST.md](host/porttool_caps/PORTTOOL-CAPS-TEST.md) |
| `host/porttool_panel/` | `python run.py --port COMx`（**真板子**）或 `--port sim`（**模拟板，不用板子**，见下），都要 playwright + Chrome | **H5** 面板在真浏览器里点一遍。判据：①页面先过一遍语法（用 playwright 自带的 node `--check`，板子都不用）②页面抛的任何异常、控制台任何 error 直接判失败 ③串口列表、未连接时的门闸、按板子分组 ④**逐个端口按一次「开始测试」，每个端口的结论必须是这台工位应该出的那一个** —— 缺激励的端口要失败，并且失败原因里要点出是哪个读数 ⑤**方案文件里的参数真的发出去了** —— `on=1:1` / `mv=1:1000` / `duty=1:100` / `mode=extloop` 在日志里能查到 ⑥**持续测试**：「单次 / 持续」两个单选，持续下面才出现时长（1/2/3/4 小时 / 一直跑）；左边可以勾多个端口、一次启动；**看门狗在续期**（日志里 `OK hold=` 一直在涨，不是只武装了一次）；点停止要同时出 `OK stopped all` 和 `OK hold=off`。⚠️ **断言看的是板子的回复不是发出去的命令** —— 续期由服务端直接走串口发，不过 `/api/command`，页面日志里没有那一行 ⑦两个 tab、日志的暂停/清空/过滤、断开、记下的控制口 ⑧**改了参数就不给结论** —— 改一个参数再按「开始测试」，结论不能是「失败」，卡片要说清哪一项和方案不一样，点「恢复方案参数」之后又能判（2026-09-11 用户实测撞出来的：勾 DO3、占空比 50，1.4 秒出一个假失败）⑨**卡片上不许剩协议词** —— 逐个端口扫一遍，命中 `BANNED_ON_CARDS` 里任何一个（`duty`、`freq`、`miss`、`Klemmblock`…）就判失败 ⑩**四个一直没被点过的控件**（2026-09-11 补）：「单独跑」单个 `pt.run` 目标、「自动回环应答」勾选框、「绑上/解开」对端串口、**方案页的「运行」按钮**（用 `bench-smoke.json` 跑完整一轮，每一步都要回判据）。⚠️ **「绑上」在模拟板上只能证明控件通到服务端并且能解开** —— 「绑对了适配器才闭合链路」只有真工位能证明，因为模拟板自己演所有对端。⚠️ 覆盖不到的是**真外观** —— 颜色间距好不好看只能人看 |
| `host/porttool_plan/` | 在 `IAPTranfer_Tool/` 下 `go test ./TestCase/...` | 判据算子（缺字段一律判失败）；执行器（超时与判据失败分得开、重试保留被它替掉的那次失败、失败后的门闸看最后一个真跑过的步骤）；随包发布的 `plans/bench-smoke.json` 和 `plans/station6-poweron.json` 都能拿假板子跑通；方案里的 `pt.run` 目标对着 caps 的 `runs=` 离线校验（打错名字、写一个固件没报过的目标，两种都要报）；方案页四个接口 —— **写盘前先验、方案名出不了 plans 目录、跑方案期间面板自己的回环应答器停摆** |
| `host/fakeboard/` | `python run_cases.py` | **K1–K7** IAPTool 在传输开始前的密钥/证书匹配决策，七种情况：自签的三种 + 委托证书的三种 + 一把密钥都没有。**每种在真板子上都要换一把 bootloader 密钥才能构造** |
| `host/crypto_ref/` | `python run_checks.py [--rounds N]` | SHA-256 构造对 hashlib（309 向量）；IAPTool 真实签名交给一份独立的纯算术 P-256 验证器 |
| `host/variant_check/` | `python build.py`，需要 arduino-cli | **P4** Arduino 变体头的编译期断言。目前一个：FMC 保留脚表（39 个）自洽。**编不过就是变体头坏了，不是 sketch 坏了** |
| `host/examples_build/` | `python build.py [--only LIB]`，需要 arduino-cli | **P5** 编译 core 自有库的**每一个 example**。⚠️ **约十分钟，故意不进 selfcheck** —— 见下 |

### P5 · example 不能腐烂

**什么时候跑**：改了 `open_plc_arduino` 的任何库之后，以及发版前。**不在 `selfcheck` 里** —— selfcheck 是"改完代码就跑"的东西，往里加十分钟只会让人不跑它。

```
python host/examples_build/build.py              # 全部
python host/examples_build/build.py --only SDRAM  # 只挑一个库
```

`tools/` 下还有四个纯静态检查，不碰任何代码执行：`check_version_sync.py`（版本号三处一致）、`check_mirror_sync.py`（跨仓镜像 9 个锚点 + RTC 备份寄存器占用）、`check_core_sync.py`（core live 与 git 仓库）、`check_public_root.py`（**P6**）。前三个对应发版检查单的 B1 / B2 / B3，以前是人工核对。

### P6 · "信任公开根"的告警不能失灵

bootloader 每次启动会在**当前生效的根就是随项目发布的那把公开根**时告警。它靠编进 `IAPServer/owner_slot.c` 的一个 SHA-256 指纹常量认出那把密钥。

### P1 · 版本号三处一致

```
python tools/check_version_sync.py       # 或 python tools/check_version_sync.py
```

比对 bootloader（`Core/Inc/IAP_config.h` 的 `OPENPLC_FW_VERSION`）、Arduino core（`boards.txt` 的 `build.fw_version`）、`RELEASE-NOTES.md` 最新的版本标题——三处本来毫无关联，各改各的。对应发版检查单 **CHK-B1**。退出码：0 三处一致，1 有分叉，2 缺文件。

### P2 · 跨仓镜像没分叉

```
python tools/check_mirror_sync.py        # 或 python tools/check_mirror_sync.py
```

`open_plc_cube_ide/docs/design/ARCHITECTURE.md` 列出的跨仓镜像代码，三个仓库没有共享构建系统，一侧改了另一侧不会报错，只会在运行时表现成不相关的症状。比的不是整份文件（C++ 侧有 `extern "C"`，两边 API 也不一样），是**每一项一个语义锚点**——只要求锚点一致。**没被检查覆盖的锚点会在输出末尾点名列出**，全绿不代表全覆盖。对应 **CHK-B2**。退出码：0 全部锚点一致，1 至少一处分叉，2 缺文件。

### P3 · core live 与 git 仓库一致

```
python tools/check_core_sync.py          # 或 python tools/check_core_sync.py
```

比对 Arduino IDE **真正加载**的那份（`$CORE_LIVE`）和本仓库 git 版（`$CORE_REPO`）。方向天生单向：改动在 `$CORE_LIVE` 里做、验证、再拷回仓库提交——`$CORE_LIVE` 不进版本控制，验证过忘了拷回来，那段代码就只活在这台机器上，重装一次 IDE 就没了。六类刻意排除在比对之外：IDE 自己的安装元数据、Go 构建产物、编辑器备份、`.claude/`、`.vscode/`。对应 **CHK-B3**。退出码：0 一致，1 有差异，2 仓库路径不对。

### P7 · 总表和用例名单不得漂

```
python tools/check_status_sync.py        # 或加 --list 只打印解析结果
```

`$PROD/docs/STATUS.md` 和 `TEST-CASES.md` 里的用例编号必须是同一个集合。抓三类漏洞：STATUS.md 拿某条用例当证据、但 TEST-CASES.md 没定义它（需求指着一条谁都跑不了的用例）；TEST-CASES.md 定义了某条用例、但没有需求在引用它（一条跑出来的结果没人记录，烂了也没人发现）；某条用例引用的需求号 STATUS.md 里不存在。退出码：0 两边一致，1 有漂移，2 缺文件。

### P8 · 一个事实只能写在一个文件里

```
python tools/check_doc_dupes.py          # 加 --min 40 只看更长的断言；--code 连代码块也列
```

把每份文档切成句子，去掉 markdown 加粗之类的强调符号（这样加粗过的一份能跟没加粗的一份对上），任何长到能算"断言"的句子出现在两个以上文件里就判失败。**指针（"见 X"）不算**——指针短且泛化，这正是修复重复的手段本身。**代码块单独报告、不计入失败**：抓下来的日志、命令这类东西合理地要在多处原样出现（发布说明要给客户看到他会看到的确切字符串，验收记录要写板子实际打了什么）——它们引的是那份 `.c` 文件，不是互相抄。退出码：0 没有断言重复，1 至少一处，2 环境问题。

### P9 · 文档里提到的路径必须存在

```
python tools/check_doc_paths.py          # 加 --list 打印它 resolve 出的每条路径
```

只检查三种能明确判断"相对谁"的写法：markdown 链接（相对当前文档）、`$BOOT`/`$TOOL`/`$CORE` 这类仓库变量路径、反引号包住的 `docs/`开头的路径（相对某个仓库根）。**故意不检查其余所有反引号路径**——一条不带仓库变量的裸路径意思是"相对这段话在讲哪个仓库"，检查脚本猜不出来。想让某条裸路径也被查到，就给它加上仓库变量前缀。路径里的行号（如 `fmc.c` 后面跟的行号范围）在检查前会被去掉——文件必须存在，行号只是提示，本来就会漂。退出码：0 每条路径都能 resolve，1 至少一条断链，2 环境问题。

### P11 · 包里的 IAPTool 不能落后于仓库

```
python tools/check_tool_sync.py           # 加 --list 连两份 usage 一起打
```

修法一条命令：`python tools/install_tool.py`（`compile_tool.sh` 末尾会自动跑它，所以正常情况下不用手动敲）。

**IDE 的 Upload 按钮跑的不是我们构建的那份 IAPTool**，而是板卡包里的副本（`$A15/packages/OpenPLC_Alpha/tools/STM32Tools/<版本>/<平台>/IAPTool`，`keys/` 也在它旁边）。所以客户手上那个二进制可以比这里所有用例测的那个落后几周，而没有任何东西会说话。比的不是哈希（同一份源码两次构建逐字节都不同，一个哭喊的检查等于没有检查），是**两个二进制自己报出来的子命令集合**：仓库有、包里没有的动词就是缺陷 —— 菜单到不了那个功能。退出码：0 包里能做到仓库能做的全部，1 落后了，2 有一份二进制不存在。

### P10 · allow 列表不许攒字面命令

```
python tools/check_allow_hygiene.py                  # 每个仓库一行汇报
python tools/check_allow_hygiene.py --list            # 连被点名的条目也打出来
python tools/check_allow_hygiene.py --fail-over 400   # 超过这个数才算失败
```

`.claude/settings.local.json` 每次人批准一条"以后别再问"，就原样追加一行——没有任何东西会删。攒到某个点，`allow` 数组里全是再也不会命中第二次的一次性记录，而真正该有通用模式的仓库反而一条没有，每条命令都弹窗。

**这条不是发版门禁**——`settings.local.json` 本机专属、不进 git，不同机器天然不同，没法当"必须全绿"的检查。默认只打印、退出码 0；只有 `--fail-over` 指定阈值且真的超了才返回 1。**判据只看"像不像一次性"**（是否带绝对路径、是否带 `-First N` 这种烤进去的输出切片），会把一些合理的本机安装路径也点出来。

## 板上测试（`onboard/`）

| 目录 | 是什么 | 怎么用 |
|---|---|---|
| `rs232/SerialPort/` | UART + USB-CDC 回显 sketch | 用 Arduino IDE/CLI 编译上传，往端子 C05/C06 发字符看回显 |
| `rs232/M5_SerialConflict/` | **M5**：`Serial4.begin()` 之后 `Serial_Test` 还能不能收 | `python tools/run_m5.py`（自己编译、烧写、发字节、验回显） |
| `sdram/SDRAM_Acceptance/` | **SD1**：`OpenPLC_SDRAM` 封装的 19 条断言 + 清零速率测量 | `python tools/run_sdram.py` |

### SD1 · SDRAM 封装（需求 E5）

sketch 打 `RESULT <名字> PASS|FAIL` 和 `MEASURE <名字> <数>`，脚本按行判。**任何 FAIL、缺 `DONE`（说明跑一半挂了）、或一条 RESULT 都没有，都算失败。**

2026-08-17 实测：`begin()` 1.4 ms，清零 **91 MB/s**（清满 64MB ≈ 701 ms），`allocUninitialized()` 0 µs。

### M5 · 诊断串口不被用户 sketch 掐掉（需求 E7）

| 判据 | 前置条件 |
|---|---|
| sketch 调过 `Serial4.begin()` 之后，往端子发 5 个字节，`Serial_Test` **全部回显** | 板子在线、COM5 接在端子上、`$ARDUINO_CLI` 已配 |

## 板级端口 · RS485（`$BOOT/TestCase/RS485/rs485_test.c`）

跑在 **bootloader** 里，不是 app：`Core/Src/main.c` 的 `RS485_TEST_ENABLE` 置 1，`python tools/flash_bootloader.py` 编译烧写，然后 `python tools/rs485_echo.py --port <适配器口> --listen 8`。USART2 经 SP3485EN 出到端子。

**前置条件**：USB-RS485 适配器 A 接端子 A10（J11-3）、B 接 A11（J11-2）；日志看端子 C05/C06（UART4）。**必须有第二台设备在 A/B 上**：PD4 同时驱动 `/RE` 和 `DE`，板子听不见自己。

⚠️ **必须有第二台设备在 A/B 上。** PD4 同时驱动 `/RE` 和 `DE`（同一条网），发的时候收就是关的，**板子听不见自己** —— 没有适配器时 R2/R4 一条都判不了。

| 编号 | 判据 | 不接线能不能判 |
|---|---|---|
| R1 | PD4 / PD5 当普通 GPIO 推 0 和 1，读回来一致 | ✅ 能，专门用来分开"没接线"和"这个脚是死的" |
| R2 | 适配器上每 **3 s** 收到一帧 `RS485 HELLO <n>` | ❌ 要适配器 |
| R4 | 主机发一串探针，板子原样发回；同时在日志口打出 **ASCII + hex** 两列 | ❌ 要适配器 |

## S4a / S4b · 掉电中断，怎么跑

```bash
python3 tools/run_s4.py --case a --bin <app.bin> --pad-to 1200000
python3 tools/run_s4.py --case b --bin <app.bin> --retry 3
```

| | |
|---|---|
| **判据 S4a** | 传输窗口内断电 → 重新上电后**旧 app 照常启动**（日志出现 `APP Mod`，且**没有** `App signature invalid or absent`）|
| **判据 S4b** | 擦写窗口内断电 → 上电报 `App signature invalid or absent`，**且重传一次能恢复** |
| **窗口锚点** | `Staging in SDRAM` 之后 / `Erasing application region` 之前 = S4a；`Erasing application region` 之后 = S4b。字符串对齐 `open_plc_cube_ide/IAPServer/IAP_server.c` |

## 未覆盖

**完整的覆盖矩阵和每条待补用例的设计骨架在 [docs/STATUS.md]($PROD/docs/STATUS.md)**，这里只留摘要：

| ID | 内容 | 为什么还没做 |
|---|---|---|
| M3 | 两块板子的 MAC 不同 | ⛔ 手上只有一块板 |
