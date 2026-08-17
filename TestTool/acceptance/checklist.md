# 验收单

**每一条都要有判据**，"看起来正常"不算通过。判据的写法沿用 [../TEST-CASES.md](../TEST-CASES.md) 的四栏格式。

三份清单用途不同，不要混：

| 清单 | 什么时候跑 | 谁跑 |
|---|---|---|
| [A · 改动后自检](#a--改动后自检) | 每次改了固件 / 工具 | 开发 |
| [B · 发版验收](#b--发版验收) | 打 tag 之前 | 开发 |
| [C · 单板出厂](#c--单板出厂) | 每一块板子 | 产线 |

---

## A · 改动后自检

由快到慢，**前一层不过就不要往下走** —— 上板调试的每一轮都比主机测试贵一个数量级。

| # | 做什么 | 判据 | 命令 |
|---|---|---|---|
| A1 | 主机侧 Go 测试 | 全过 | 在 `IAPTranfer_Tool/` 下 `go test ./TestTool/...` |
| A2 | 主机侧 C 测试 | 全过 | `host/bootloader_unit/build.ps1` —— ⚠️ **需要主机 gcc/clang，这台机器上没有**，2026-08-17 未能执行 |
| A3 | 整模块静态检查 | 无输出 | `go vet ./...` |
| A4 | bootloader 构建 | **0 errors 0 warnings**，且 `.bin` ≤ 131,072 B | `tools/flash-bootloader.ps1`（构建阶段会打占用率） |
| A5 | 烧写 + 启动日志 | 见 [T0](#t0--启动门禁) | `tools/flash-bootloader.ps1` |
| A6 | 设备行为用例 | 全过 | `TestTool all --ip=<板子IP>` |

⚠️ A4 那个尺寸不是形式检查：bootloader 只有一个 128K 扇区，**超了链接器会报 `region FLASH overflowed`**，而 owner 记录区将来还要从同一个扇区尾部划走一块。

---

## B · 发版验收

先跑完 A，再跑这里。

| # | 做什么 | 判据 |
|---|---|---|
| B1 | 版本号三处一致 | `IAP_config.h` 的 `OPENPLC_FW_VERSION` == core `boards.txt` 的 `build.fw_version` == 发布说明 |
| B2 | 跨仓镜像代码同步 | `open_plc_cube_ide/docs/ARCHITECTURE.md`「跨仓镜像的代码」表里每一项两边一致 |
| B3 | Arduino 包已同步进 git | `$CORE_LIVE` 与 `$CORE_REPO` 逐文件一致（比对命令在 ARCHITECTURE.md） |
| B4 | 密钥已轮换 | 不再是占位密码 / 占位签名私钥，且私钥已移出构建机 |
| B5 | 捆绑升级风险已写进发布说明 | `open_plc_cube_ide/RELEASE-NOTES.md` 的 Upgrade rules 与当前 journal 格式相符 |
| B6 | 全新板子路径 | 一块从未烧过 app 的板子：`BOOTLD-INVALID` → 上传 → 正常启动 |
| B7 | 升级路径 | 一块跑着**上一版**的板子：先烧 bootloader，再传 app，正常启动 |

⚠️ **B7 是唯一能抓住捆绑升级风险的用例。** 只测 B6 永远发现不了"新 bootloader 读不懂旧 journal"。

---

## C · 单板出厂

产线逐板执行。**每条都要能在几十秒内判完**，否则产线跑不动。

| # | 做什么 | 判据 |
|---|---|---|
| C1 | 烧 bootloader | ST-Link 报下载完成且校验通过 |
| C2 | 启动日志 | 有 `SDRAM staging buffer OK`；`Reset cause` 合理；crypto 自检未报错 |
| C3 | **MAC 唯一** | 记录本板 MAC，**和已出货记录比对不重复** |
| C4 | 以太网 | 拿到 IP，UDP 发现有应答 |
| C5 | USB CDC | 枚举出串口，`ping` 答 `OK` |
| C6 | RS232 | `onboard/rs232/SerialPort` 回显正常 |
| C7 | 烧 app | 上传成功并正常启动 |

### ⚠️ C3 现在做不了，但必须做

MAC 由芯片 UID 派生，**「两块板互不相同」从未被观察过** —— 手上只有一块板。派生算法要是有缺陷，量产时表现为同网段大面积 IP 冲突，而那时已经晚了。

**拿到第二块板的第一件事就是验这条**（对应 [../TEST-CASES.md](../TEST-CASES.md) 未覆盖表里的 M3）。产线要留 MAC 记录，否则"不重复"无从判起。

---

## T0 · 启动门禁

任何上板测试之前都先过这一关。`tools/flash-bootloader.ps1` 会自动判。

| 日志 | 含义 | 接下来 |
|---|---|---|
| `SDRAM staging buffer OK (2 MiB at C0000000)` | ✅ 暂存区可用 | 继续 |
| `** SDRAM SELF-TEST FAILED at offset ... **` | ❌ FMC 或上电时序坏了 | **停**。先修 FMC，上传测试全部无意义 |
| 串口一个字节都没有 | 日志口被占用，或 UART4 没接 | 看脚本提示的占用进程；或改用 SWO/ITM |

自检失败**不改变任何控制流** —— 板子仍然安全（上传会在 CRC 那步失败、app 区不受影响），这行日志的作用只是把根因直接说出来，省掉"为什么每次都 Checksum Failed"的排查。
