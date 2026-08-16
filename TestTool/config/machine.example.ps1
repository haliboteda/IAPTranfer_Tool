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

# --- board ------------------------------------------------------------------
# LOG_PORT: where the bootloader/app printf lands. UART4 reaches RS232 terminals
# C05/C06 -- real +/-12V levels, so this is an RS232 adapter, not a TTL one.
# Put the most likely port first; tools/ try them in order.
$LOG_PORTS = @("COM5", "COM4")
$LOG_BAUD  = 115200
$CDC_PORT  = "COM6"                                  # board's USB CDC, when enumerated
$BOARD_IP  = "192.168.0.7"
