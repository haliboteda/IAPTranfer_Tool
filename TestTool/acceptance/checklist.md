# 验收单

**每一条都要有判据**，"看起来正常"不算通过。判据的写法沿用 [../TEST-CASES.md](../TEST-CASES.md) 的四栏格式。

三份清单用途不同，不要混：

| 清单 | 什么时候跑 | 谁跑 |
|---|---|---|
| [CHK-A · 改动后自检](#chk-a--改动后自检) | 每次改了固件 / 工具 | 开发 |
| [CHK-B · 发版验收](#chk-b--发版验收) | 打 tag 之前 | 开发 |
| [CHK-C · 单板出厂](#chk-c--单板出厂) | 每一块板子 | 产线 |

---

## CHK-A · 改动后自检

由快到慢，**前一层不过就不要往下走** —— 上板调试的每一轮都比主机测试贵一个数量级。

| # | 做什么 | 判据 | 命令 |
|---|---|---|---|
| CHK-A1 | 主机侧 Go 测试（用例 **H1**） | 全过 | 见 [../TEST-CASES.md](../TEST-CASES.md) 的 host 层表 |
| CHK-A2 | 主机侧 C 测试（用例 **H2**） | 全过 | `host/bootloader_unit/build.py` —— 编译器路径填 `config/machine.py` 的 `HOST_CC` |
| CHK-A3 | 整模块静态检查（用例 **H3**） | 无输出 | `go vet ./...` |
| CHK-A4 | bootloader 构建 | **0 errors 0 warnings**，且 `.bin` ≤ **122,880 B** | `tools/flash_bootloader.py`（构建阶段会打占用率） |
| CHK-A5 | 烧写 + 启动日志 | 见 [BG1](#bg1--启动门禁) | `tools/flash_bootloader.py` |
| CHK-A6 | 设备行为用例 | 全过 | `TestTool all --ip=<板子IP> --bin=<app.bin> --password-file=<...>` |
| CHK-A7 | 变体断言 + 公开根指纹（用例 **P4** / **P6**） | 全过 | 都在 `tools/selfcheck.py` 里 |

**CHK-A1–A3、CHK-A7 一条命令跑完：`tools/selfcheck.py`**（`--list` 先看它会跑哪 15 步）。

⚠️ **CHK-A4 的上限是 122,880 不是 131,072。** 扇区确实是 128K，但**尾部 8K 已经划给 owner 记录区**（需求 C10，2026-08-18），链接脚本只把 120K 给链接器。按 131,072 判会多算 8K 余量，并且掩盖真正开始失败的那个点。超了链接器会报 `region FLASH overflowed`。

⚠️ **CHK-A6 里 `all` 不含要人动手的用例**（AU1、OW1、OW3），它们会被点名跳过而不是静默略过。要跑得单独按 id 跑，见 [../TEST-CASES.md](../TEST-CASES.md)。

---

## CHK-B · 发版验收

先跑完 A，再跑这里。

| # | 做什么 | 判据 |
|---|---|---|
| CHK-B1 | 版本号三处一致（用例 **P1**） | `IAP_config.h` 的 `OPENPLC_FW_VERSION` == core `boards.txt` 的 `build.fw_version` == 发布说明 |
| CHK-B2 | 跨仓镜像代码同步（用例 **P2**） | `open_plc_cube_ide/docs/design/ARCHITECTURE.md`「跨仓镜像的代码」表里每一项两边一致 |
| CHK-B3 | Arduino 包已同步进 git（用例 **P3**） | `$CORE_LIVE` 与 `$CORE_REPO` 逐文件一致（比对命令在 ARCHITECTURE.md） |
| CHK-B4 | 密钥已轮换 | 不再是占位密码 / 占位签名私钥，且私钥已移出构建机 |
| CHK-B5 | 捆绑升级风险已写进发布说明 | `open_plc_cube_ide/RELEASE-NOTES.md` 的 Upgrade rules 与当前 journal 格式相符 |
| CHK-B6 | 全新板子路径 | 一块从未烧过 app 的板子：`BOOTLD-INVALID` → 上传 → 正常启动 |
| CHK-B7 | 升级路径 | 一块跑着**上一版**的板子：先烧 bootloader，再传 app，正常启动 |

⚠️ **CHK-B7 是唯一能抓住捆绑升级风险的用例。** 只测 CHK-B6 永远发现不了"新 bootloader 读不懂旧 journal"。

---

## CHK-C · 单板出厂

产线逐板执行。**每条都要能在几十秒内判完**，否则产线跑不动。

| # | 做什么 | 判据 |
|---|---|---|
| CHK-C1 | 烧 bootloader | ST-Link 报下载完成且校验通过 |
| CHK-C2 | 启动日志 | 有 `SDRAM staging buffer OK`；`Reset cause` 合理；crypto 自检未报错 |
| CHK-C3 | **MAC 唯一** | 记录本板 MAC，**和已出货记录比对不重复** |
| CHK-C4 | 以太网 | 拿到 IP，UDP 发现有应答 |
| CHK-C5 | USB CDC | 枚举出串口，`ping` 答 `OK` |
| CHK-C6 | RS232 | `onboard/rs232/SerialPort` 回显正常 |
| CHK-C7 | 烧 app | 上传成功并正常启动 |

### ⚠️ CHK-C3 现在做不了，但必须做

MAC 由芯片 UID 派生，**「两块板互不相同」从未被观察过** —— 手上只有一块板。派生算法要是有缺陷，量产时表现为同网段大面积 IP 冲突，而那时已经晚了。

**拿到第二块板的第一件事就是验这条**（对应 [../TEST-CASES.md](../TEST-CASES.md) 未覆盖表里的 M3）。产线要留 MAC 记录，否则"不重复"无从判起。

---

## BG1 · 启动门禁

任何上板测试之前都先过这一关。`tools/flash_bootloader.py` 会自动判。

⚠️ **这条 2026-08-22 之前叫 `T0`。** 它不属于 `T1`–`T4` 那一系列（那些是设备行为用例，定义在 [../TEST-CASES.md](../TEST-CASES.md)，`T0` 从来不在那里），所以给了它自己的前缀。

| 日志 | 含义 | 接下来 |
|---|---|---|
| `SDRAM staging buffer OK (2 MiB at C0000000)` | ✅ 暂存区可用 | 继续 |
| `** SDRAM SELF-TEST FAILED at offset ... **` | ❌ FMC 或上电时序坏了 | **停**。先修 FMC，上传测试全部无意义 |
| 串口一个字节都没有 | 日志口被占用，或 UART4 没接 | 看脚本提示的占用进程；或改用 SWO/ITM |

**当前状态：❌ 2026-08-21 失败，3/3 复现**（SDRAM 的 D1 线导通极弱）。原因和下一步在 `open_plc_cube_ide/docs/work/investigations/sdram-d1.md`。

自检失败**不改变任何控制流** —— 板子仍然安全（上传会在 CRC 那步失败、app 区不受影响），这行日志的作用只是把根因直接说出来，省掉"为什么每次都 Checksum Failed"的排查。
