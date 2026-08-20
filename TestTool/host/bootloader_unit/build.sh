#!/usr/bin/env bash
# Builds and runs the host-side IAP security test harness against the real
# bootloader source in open_plc_cube_ide/IAPServer. Needs any C11 compiler
# (gcc or clang) on PATH; override with CC=clang ./build.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Where the bootloader repo is comes from config/machine.ps1, not from counting
# "../" upwards: that chain silently broke the moment this directory moved, and
# it was wrong on any machine with a different layout anyway. Only the one
# assignment is read, so the PowerShell file stays the single source of truth
# rather than being duplicated into a .sh twin that would drift.
CFG="$HERE/../../config/machine.ps1"
if [ ! -f "$CFG" ]; then
	echo "error: config/machine.ps1 is missing. Generate it -- this machine's" >&2
	echo "       paths are detected, not typed:  python3 tools/init_machine.py" >&2
	exit 1
fi
# Both quote styles: hand-written configs used double quotes, and the generated
# one uses single quotes so that a $ or a backtick in a path cannot be
# interpolated by PowerShell. Reading only one style would leave this failing
# with an empty BOOT_REPO and no clue why.
BOOT_REPO="$(sed -n -e "s/^\\\$BOOT_REPO[[:space:]]*=[[:space:]]*'\\(.*\\)'.*/\\1/p" \
                    -e 's/^\$BOOT_REPO[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' "$CFG" | head -1)"
if [ -z "$BOOT_REPO" ]; then
	echo "error: \$BOOT_REPO not found in $CFG" >&2
	exit 1
fi
# Accept the Windows path this file naturally holds when running under Git Bash / WSL.
if command -v cygpath >/dev/null 2>&1; then
	BOOT_REPO="$(cygpath -u "$BOOT_REPO")"
fi

IAPSERVER="$BOOT_REPO/IAPServer"
CC="${CC:-gcc}"

if ! command -v "$CC" >/dev/null 2>&1; then
	echo "error: no C compiler '$CC' found on PATH (install gcc/clang, or set CC=...)" >&2
	exit 1
fi

"$CC" -std=c11 -Wall -Wextra -O0 -g \
	-I "$HERE/stubs" \
	-I "$IAPSERVER" \
	"$HERE/stubs/hal_stub.c" \
	"$HERE/stubs/bootloader_state_stub.c" \
	"$IAPSERVER/sha256.c" \
	"$IAPSERVER/iap_keyderive.c" \
	"$IAPSERVER/iap_auth.c" \
	"$HERE/test_main.c" \
	-o "$HERE/iap_hosttest"

"$HERE/iap_hosttest"
