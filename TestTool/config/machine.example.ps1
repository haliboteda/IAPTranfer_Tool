# Everything that differs between machines lives here and nowhere else.
#
# Copy to machine.ps1 and edit. machine.ps1 is gitignored; this template is not,
# so a new machine only ever has to fill in these values.
#
# Scripts under tools/ dot-source it:  . "$PSScriptRoot/_common.ps1"
#
# What does NOT belong here: anything that follows from the platform. Executable
# suffixes, the win/linux/macosx directory inside the Arduino package, the Go
# output directory, the CubeIDE plugin suffix -- tools/_common.ps1 derives all of
# those from $PLATFORM. Put a value here only if a second machine running the
# same OS could legitimately differ.
#
# Both a Windows and a Linux example is given for every path. Delete the one you
# are not on; keeping both in machine.ps1 means the second assignment silently
# wins.

# --- repositories -----------------------------------------------------------
# The three repos this product is built from. See open_plc_cube_ide/docs/ARCHITECTURE.md.
$BOOT_REPO = "E:\WorkSpace\Schaeffer-AG\open_plc_cube_ide"
$CORE_REPO = "E:\WorkSpace\Schaeffer-AG\open_plc_arduino"
$TOOL_REPO = "E:\WorkSpace\Schaeffer-AG\IAPTranfer_Tool"
# $BOOT_REPO = "$HOME/work/schaeffer/open_plc_cube_ide"
# $CORE_REPO = "$HOME/work/schaeffer/open_plc_arduino"
# $TOOL_REPO = "$HOME/work/schaeffer/IAPTranfer_Tool"

# Arduino's data directory, and the installed board package inside it. $CORE_LIVE
# is the copy the IDE actually compiles against and is NOT under version control
# -- see ARCHITECTURE.md for the live -> repo direction. The trailing 0.1.3-pre is
# the package version and changes on release.
$A15       = "$env:LOCALAPPDATA\Arduino15"
$CORE_LIVE = "$A15\packages\OpenPLC_Alpha\hardware\stm32\0.1.3-pre"
# $A15       = "$HOME/.arduino15"
# $CORE_LIVE = "$A15/packages/OpenPLC_Alpha/hardware/stm32/0.1.3-pre"

# --- toolchain --------------------------------------------------------------
# CubeIDE supplies both the headless builder and STM32_Programmer_CLI. Point this
# at the install root; _common.ps1 resolves the versioned plugin and the
# per-platform executable names underneath it.
$CUBEIDE   = "D:\ST\STM32CubeIDE_1.10.0"
$WORKSPACE = "E:\WorkSpace\Schaeffer-AG"            # Eclipse workspace holding the bootloader project
# $CUBEIDE   = "/opt/st/stm32cubeide_1.10.0"
# $WORKSPACE = "$HOME/work/schaeffer"

# IAPTool ships inside the board package. Leave this empty and _common.ps1 finds
# it by wildcard under $A15 for the current platform -- which is what you want,
# because the package version moves independently of the core version. Set it
# only to force a specific build.
$IAPTOOL   = ""

# Arduino IDE 2.x, for rebuilding the application from the command line. The CLI
# it ships is NOT on PATH and is not the standalone arduino-cli release; the
# config file is what points it at Arduino15 packages and the user libraries.
$IDE       = "D:\Soft\arduino-2"
$ARDUINO_CLI = "$IDE\resources\app\lib\backend\resources\arduino-cli.exe"
$ARDUINO_CLI_CONFIG = "$HOME\.arduinoIDE\arduino-cli.yaml"
# $IDE       = "/opt/arduino-ide"
# $ARDUINO_CLI = "$IDE/resources/app/lib/backend/resources/arduino-cli"
# $ARDUINO_CLI_CONFIG = "$HOME/.arduinoIDE/arduino-cli.yaml"

# Host C compiler for host/bootloader_unit (case H2), which compiles the real
# bootloader C sources natively. Leave "" to fall back to whatever "gcc" resolves
# to on PATH -- the normal answer on Linux. selfcheck then reports H2 as SKIP and
# names it, rather than pretending it passed.
#
# ⚠️ It must be a MODERN gcc/clang. A Dev-C++ install ships GCC 3.4.2 (2004):
# it rejects -std=c11 outright, and at -std=c99 its linker crashes. Verifying
# code destined for arm-none-eabi-gcc 12.x with a twenty-year-old compiler
# would be worse than not testing it.
$HOST_CC   = "D:\Soft\mingw64\bin\gcc.exe"
# $HOST_CC   = ""

# --- board ------------------------------------------------------------------
# LOG_PORTS: where the bootloader/app printf lands. UART4 reaches RS232 terminals
# C05/C06 -- real +/-12V levels, so this is an RS232 adapter, not a TTL one.
# Put the most likely port first; tools/ try them in order.
#
# On Linux the device name depends on the adapter chip: /dev/ttyUSB* for FTDI and
# CH340, /dev/ttyACM* for CDC devices including the ST-Link VCP. The user must be
# in the dialout group or every open fails with a permission error.
$LOG_PORTS = @("COM5", "COM4")
# $LOG_PORTS = @("/dev/ttyUSB0", "/dev/ttyACM0")
$LOG_BAUD  = 115200
$CDC_PORT  = "COM6"                                  # board's USB CDC, when enumerated
# $CDC_PORT  = "/dev/ttyACM1"
$BOARD_IP  = "192.168.0.7"
