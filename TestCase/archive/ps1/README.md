# 归档：PowerShell 版测试脚本

**这些文件不再运行，也不再维护。** 留在这里只为一件事：**复查**——当某个 Python 版的行为可疑时，能翻出它翻译自什么。

> **以后测试脚本一律用 Python**（用户 2026-09-01 定）。新脚本不要写 `.ps1`，也不要往这个目录里加东西。

## 目录结构照搬原位置

`archive/ps1/tools/run-m5.ps1` 就是原来的 `tools/run-m5.ps1`。这样一眼能看出它当初住在哪、和谁在一起。

## 对照表

| 归档的 ps1 | 现在用哪个 |
|---|---|
| `tools/_common.ps1` | `tools/common.py` |
| `tools/check-core-sync.ps1` | `tools/check_core_sync.py` |
| `tools/check-mirror-sync.ps1` | `tools/check_mirror_sync.py` |
| `tools/check-public-root.ps1` | `tools/check_public_root.py` |
| `tools/check-version-sync.ps1` | `tools/check_version_sync.py` |
| `tools/enter-bootloader.ps1` | `tools/enter_bootloader.py` |
| `tools/flash-bootloader.ps1` | `tools/flash_bootloader.py` |
| `tools/inject-owner-record.ps1` | `tools/inject_owner_record.py` |
| `tools/run-au1.ps1` | `tools/run_au1.py` |
| `tools/run-case.ps1` | `tools/run_case.py` |
| `tools/run-m5.ps1` | `tools/run_m5.py` |
| `tools/run-s3.ps1` | `tools/run_s3.py` |
| `tools/run-sdram.ps1` | `tools/run_sdram.py` |
| `tools/run-setowner.ps1` | `tools/run_setowner.py` |
| `tools/run-takeown.ps1` | `tools/run_takeown.py` |
| `tools/selfcheck.ps1` | `tools/selfcheck.py` |
| `tools/serial-watch.ps1` | `tools/serial_watch.py` |
| `tools/upload-and-watch.ps1` | `tools/upload_and_watch.py` |
| `host/bootloader_unit/build.ps1` | `host/bootloader_unit/build.py` |
| `host/crypto_ref/run-checks.ps1` | `host/crypto_ref/run_checks.py` |
| `host/examples_build/build.ps1` | `host/examples_build/build.py` |
| `host/fakeboard/run-cases.ps1` | `host/fakeboard/run_cases.py` |
| `host/fakeboard/run-downgrade.ps1` | `host/fakeboard/run_downgrade.py` |
| `host/variant_check/build.ps1` | `host/variant_check/build.py` |
| **`tools/m7-compare.ps1`** | **没有，也不会有** —— 见下 |
| **`tools/m7-compare-faults.ps1`** | **没有，也不会有** |

## M7 那两个比对脚本为什么不移植

它们存在的唯一目的是**拿 PS 版和 Python 版对跑、逐字节比对输出**。PS 版一旦归档，比对就没有对象了，移植成 Python 是空转。

归档前跑了一次留作最终证据：

- `M7-BASELINE-compare.txt` —— `m7-compare.ps1` 的完整输出
- `M7-BASELINE-faults.txt` —— `m7-compare-faults.ps1` 的完整输出（如果存在）

⚠️ **这两份基线只覆盖静态检查和 host 套件。** 板级 `run-*` 脚本要烧写、读串口、动 owner 槽，**两版没法对跑**，M7 从来就覆盖不到它们。那几个 Python 版的等价性只能靠真板子跑一遍确认，状态见 `TestCase/TEST-CASES.md`。

## 想跑归档里的某个 ps1 怎么办

它们 `. "$PSScriptRoot/_common.ps1"` 的相对路径在这个目录里仍然成立（`tools/` 下的还在 `tools/` 下），但 `config/machine.ps1` 已经不在 `../config/` 了。真要跑，从 git 历史里取当时那一版，比在这里就地修补可靠。
