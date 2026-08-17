# Everything that differs between machines lives here and nowhere else.
#
# Copy to machine.ps1 and edit. machine.ps1 is gitignored; this template is not,
# so a new machine only ever has to fill in these values.
#
# Scripts under tools/ dot-source it:  . "$PSScriptRoot\..\config\machine.ps1"

# --- repositories -----------------------------------------------------------
# The three repos this product is built from. See open_plc_cube_ide/docs/ARCHITECTURE.md.
$BOOT_REPO = "E:\WorkSpace\Schaeffer-AG\open_plc_cube_ide"      # bootloader, CubeIDE project
$CORE_LIVE = "C:\Users\<you>\AppData\Local\Arduino15\packages\OpenPLC_Alpha\hardware\stm32\0.1.3-pre"
$CORE_REPO = "E:\WorkSpace\Schaeffer-AG\open_plc_arduino"
$TOOL_REPO = "E:\WorkSpace\Schaeffer-AG\IAPTranfer_Tool"

# --- toolchain --------------------------------------------------------------
# CubeIDE supplies both the headless builder and STM32_Programmer_CLI. The
# programmer sits under plugins/ and its directory name carries a version, so
# tools/ resolves it by wildcard rather than hardcoding it here.
$CUBEIDE   = "D:\ST\STM32CubeIDE_1.10.0"
$WORKSPACE = "E:\WorkSpace\Schaeffer-AG"            # Eclipse workspace holding the bootloader project
$IAPTOOL   = "$CORE_LIVE\..\..\..\tools\STM32Tools\0.1.2\win\IAPTool.exe"

# Arduino IDE 2.x, for rebuilding the application from the command line. The CLI
# it ships is NOT on PATH and is not the standalone arduino-cli release; the
# config file is what points it at Arduino15 packages and the user libraries.
$IDE       = "D:\Soft\arduino-2"
$ARDUINO_CLI = "$IDE\resources\app\lib\backend\resources\arduino-cli.exe"
$ARDUINO_CLI_CONFIG = "$HOME\.arduinoIDE\arduino-cli.yaml"

# Host C compiler for host/bootloader_unit (case H2), which compiles the real
# bootloader C sources natively. Leave "" if there is none -- selfcheck then
# reports H2 as SKIP and names it, rather than pretending it passed.
#
# ⚠️ It must be a MODERN gcc/clang. A Dev-C++ install ships GCC 3.4.2 (2004):
# it rejects -std=c11 outright, and at -std=c99 its linker crashes. Verifying
# code destined for arm-none-eabi-gcc 12.x with a twenty-year-old compiler
# would be worse than not testing it.
$HOST_CC   = "D:\Soft\mingw64\bin\gcc.exe"

# --- board ------------------------------------------------------------------
# LOG_PORT: where the bootloader/app printf lands. UART4 reaches RS232 terminals
# C05/C06 -- real +/-12V levels, so this is an RS232 adapter, not a TTL one.
# Put the most likely port first; tools/ try them in order.
$LOG_PORTS = @("COM5", "COM4")
$LOG_BAUD  = 115200
$CDC_PORT  = "COM6"                                  # board's USB CDC, when enumerated
$BOARD_IP  = "192.168.0.7"
