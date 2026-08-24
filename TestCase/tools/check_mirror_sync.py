"""Checks the cross-repo mirrored code listed in
open_plc_cube_ide/docs/design/ARCHITECTURE.md ("跨仓镜像的代码").

Those copies cannot be enforced by any build system -- three repos, no shared
build -- so a one-sided edit diverges silently and only shows up at runtime as
some unrelated-looking symptom. This script compares a semantic anchor per item
rather than whole files: the files legitimately differ (extern "C" in the C++
core, different surrounding APIs), only the anchors must agree.

Exit code 0 = every anchor agrees, 1 = at least one diverged, 2 = a file the
check needs is missing.

Anchors that are NOT checked here are listed at the bottom of the output, so
"all green" never reads as "everything is covered".

M7 step 2: a translation of check-mirror-sync.ps1.

Two PowerShell behaviours are load-bearing here and are reproduced explicitly:
the -replace and -match operators are case-INsensitive while [regex]::Match is
case-sensitive, and Select-Object -Unique compares case-insensitively. Getting
either wrong changes verdicts rather than formatting.
"""

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Warn, Fail, read_text,        # noqa: E402
                    get_iap_tool)

failed = 0
skipped = 0


def get_anchor(path, pattern, all_matches=False):
    """Pull capture-group-1 out of a file. Missing file or zero matches both
    return None so the caller can tell them apart from ""."""
    if not Path(path).exists():
        return None
    matches = list(re.finditer(pattern, read_text(path)))
    if not matches:
        return None
    if all_matches:
        return "|".join(m.group(1).strip() for m in matches)
    return matches[0].group(1).strip()


def compare_anchor(name, sides):
    """One row of the report. `sides` maps label -> extracted value; they must
    all be equal and non-None."""
    global failed, skipped

    missing = [k for k, v in sides.items() if v is None]
    if missing:
        Warn("SKIP  %s" % name)
        for k in missing:
            Warn("        not found in: %s" % k)
        skipped += 1
        return

    # Select-Object -Unique is case-insensitive.
    values = list(dict.fromkeys(v.lower() for v in sides.values()))
    if len(values) == 1:
        Ok("OK    %s" % name)
        return

    Fail("DIFF  %s" % name)

    # Anchors built from many ";"-joined parts (the FMC pin map is 39 of them)
    # are longer than any sensible line, and truncating them printed two
    # identical-looking lines that differed somewhere past the cut. Report the
    # parts that actually differ instead.
    parts = [v.split(';') for v in sides.values()]
    is_multi_part = sum(1 for p in parts if len(p) > 1) == len(sides)
    common = None
    if is_multi_part:
        common = parts[0]
        for p in parts:
            common = [c for c in common if c in p]

    for k, v in sides.items():
        if is_multi_part:
            only = [x for x in v.split(';') if x not in common]
            Fail("        %-46s only here: %s" % (k, " ".join(only)))
        else:
            if len(v) > 120:
                v = v[:117] + "..."
            Fail("        %-46s %s" % (k, v))
    if is_multi_part:
        print("        (%d part(s) agree and are not shown)" % len(common))
    failed += 1


BOOT = Path(cfg.BOOT_REPO)
LIVE = Path(cfg.CORE_LIVE)
TOOL = Path(cfg.TOOL_REPO)

boot_udp = BOOT / "IAPServer/udp_server.c"
core_udp = LIVE / "libraries/OpenPLC_IAP/src/udp_server.c"
boot_eth = BOOT / "LWIP/Target/ethernetif.c"
core_eth = LIVE / "libraries/OpenPLC_Net/src/ethernetif.c"
boot_srv = BOOT / "IAPServer/IAP_server.c"
boot_hand = BOOT / "IAPServer/IAP_boot_handoff.h"
core_hand = LIVE / "cores/arduino/stm32/IAP_boot_handoff.h"
tool_lock = TOOL / "uploadlock.go"
core_disc = LIVE / "tools/discovery/network_discovery.go"
boot_fmc = BOOT / "Core/Src/fmc.c"
core_variant = LIVE / "variants/STM32H7xx/H743/variant_PLC_H743.h"
boot_pwd = BOOT / "IAPServer/keys/iap_fixed_password.txt"
core_pwd = LIVE / "libraries/OpenPLC_IAP/src/keys/iap_fixed_password.txt"
# Next to the shipped IAPTool, whose package version and platform directory both
# move independently of the core -- so take the directory the tool was found in
# rather than spelling either of them out.
ship_pwd = get_iap_tool().parent / "keys/iap_fixed_password.txt"

Section("cross-repo mirrors")

# --- discovery rate limiter -------------------------------------------------
# Divergence here means one image throttles and the other does not, which is
# exactly the bug that produced the 2026-08-15 "board disappears" reports.
compare_anchor("discovery reply cap (replies/sec)", {
    "bootloader IAPServer/udp_server.c":
        get_anchor(boot_udp, r'#define\s+DISCOVERY_MAX_REPLIES_PER_SEC\s+(\d+)'),
    "core OpenPLC_IAP/src/udp_server.c":
        get_anchor(core_udp, r'#define\s+DISCOVERY_MAX_REPLIES_PER_SEC\s+(\d+)'),
})

compare_anchor("discovery rate-limit window (ms)", {
    "bootloader IAPServer/udp_server.c": get_anchor(boot_udp, r'window_start\)\s*>=\s*(\d+)'),
    "core OpenPLC_IAP/src/udp_server.c": get_anchor(core_udp, r'window_start\)\s*>=\s*(\d+)'),
})


# --- MAC derivation ---------------------------------------------------------
# Two boards on one LAN collide if this diverges, and it only shows up in the
# field. The array is named MACAddr on one side and mac on the other, so the
# byte assignments are normalised before comparing.
#
# Both files also contain byte assignments that are NOT the derivation -- the
# CubeMX-generated constant MAC in the bootloader, the MAC_ADDR0..5 override
# branch in the core -- so only assignments whose right-hand side comes from the
# UID are collected. Anything else is a different code path.
def get_mac_derivation(path):
    if not Path(path).exists():
        return None
    text = read_text(path)
    text = re.sub(r'MACAddr', 'M', text, flags=re.I)    # -replace: case-insensitive
    text = re.sub(r'\bmac\b', 'M', text, flags=re.I)
    hash_m = re.search(r'h\s*=\s*(u0[^;]+);', text)     # [regex]::Match: case-sensitive
    if not hash_m:
        return None
    parts = [hash_m.group(1)]
    for b in re.finditer(r'M\[(\d)\]\s*=\s*([^;]+);', text):
        rhs = b.group(2)
        if not re.search(r'\bh\b|\bu0\b|0x02U', rhs, re.I):     # -notmatch: case-insensitive
            continue
        parts.append("%s=%s" % (b.group(1), rhs))
    if len(parts) != 7:
        return None
    return re.sub(r'\s+', '', ";".join(parts))


compare_anchor("MAC derived from UID", {
    "bootloader LWIP/Target/ethernetif.c": get_mac_derivation(boot_eth),
    "core OpenPLC_Net/src/ethernetif.c": get_mac_derivation(core_eth),
})

# --- identity string --------------------------------------------------------
# The PC tool splits the reply on "_"; a format change on one side alone makes
# that side's boards unparseable.
compare_anchor("identity string format", {
    "bootloader IAPServer/IAP_server.c": get_anchor(boot_srv, r'snprintf\([^;]*?"(%s_[^"]*)"'),
    "core OpenPLC_IAP/src/udp_server.c": get_anchor(core_udp, r'snprintf\([^;]*?"(%s_[^"]*)"'),
})


# --- SRAM4 handoff record ---------------------------------------------------
# A layout or magic mismatch means the app's "stay in the bootloader" request is
# read as garbage. The record fails towards staying in the bootloader, so the
# symptom is a board that will not boot its app rather than one that ignores the
# request -- still worth catching before it ships.
def get_handoff_layout(path):
    if not Path(path).exists():
        return None
    text = read_text(path)
    parts = []
    for k in ('BOOT_HANDOFF_ADDR', 'BOOT_HANDOFF_SIZE',
              'BOOT_HANDOFF_MAGIC', 'BOOT_HANDOFF_VERSION'):
        m = re.search(r'#define\s+%s\s+(\S+)' % k, text)
        if not m:
            return None
        parts.append("%s=%s" % (k, m.group(1)))
    for m in re.finditer(r'BOOT_REQ_(\w+)\s*=\s*(\d+)', text):
        parts.append("%s=%s" % (m.group(1), m.group(2)))
    st = re.search(r'typedef struct \{(.*?)\} boot_handoff_t;', text, re.S)
    if not st:
        return None
    for m in re.finditer(r'(uint\d+_t)\s+(\w+)\s*;', st.group(1)):
        parts.append("%s %s" % (m.group(1), m.group(2)))
    return ";".join(parts)


compare_anchor("SRAM4 boot_handoff_t layout", {
    "bootloader IAPServer/IAP_boot_handoff.h": get_handoff_layout(boot_hand),
    "core cores/arduino/stm32/IAP_boot_handoff.h": get_handoff_layout(core_hand),
})


# --- FMC pin map ------------------------------------------------------------
# The variant header names the 39 SDRAM pins (FMC_RESERVED_*) so a user can see
# what not to drive; fmc.c is where they are actually configured. That makes the
# header a second copy, and a one-sided change makes it lie -- it would still
# claim PE7 is a data line after PE7 stopped being one, which is worse than not
# listing the pins at all, because E6's whole value is that the list is true.
#
# The variant's own FMC_RESERVED_PIN_COUNT assertion cannot catch this: it only
# proves the header is self-consistent, and it stays self-consistent while fmc.c
# moves underneath it.
#
# Normalised to a sorted set of FUNC=PIN on both sides:
#   fmc.c    "  PE7   ------> FMC_D4"        -> D4=PE7
#   variant  "#define FMC_RESERVED_D4  PE7"  -> D4=PE7
def get_fmc_pin_map(path, pattern, pin_group, func_group):
    if not Path(path).exists():
        return None
    pairs = []
    for m in re.finditer(pattern, read_text(path)):
        pairs.append("%s=%s" % (m.group(func_group), m.group(pin_group)))
    if not pairs:
        return None
    # fmc.c carries the same block twice (MspInit and MspDeInit); dedupe rather
    # than compare a doubled list against a single one.
    return ";".join(sorted(set(pairs)))


compare_anchor("FMC pin map (39 SDRAM pins)", {
    "bootloader Core/Src/fmc.c":
        get_fmc_pin_map(boot_fmc, r'(P[A-I]\d+)\s*-+>\s*FMC_(\w+)', 1, 2),
    "core variants/.../variant_PLC_H743.h":
        get_fmc_pin_map(core_variant, r'#define\s+FMC_RESERVED_(\w+)\s+(P[A-I]\d+)', 2, 1),
})

# --- upload lock ------------------------------------------------------------
# IAPTool and network_discovery coordinate purely through this file. A name
# mismatch means neither sees the other and the IDE's poller talks over an
# upload in progress.
compare_anchor("upload lock filename", {
    "IAPTool uploadlock.go": get_anchor(tool_lock, r'uploadLockName\s*=\s*"([^"]+)"'),
    "core tools/discovery/network_discovery.go":
        get_anchor(core_disc, r'uploadLockName\s*=\s*"([^"]+)"'),
})

compare_anchor("upload lock max age", {
    "IAPTool uploadlock.go": get_anchor(tool_lock, r'UploadLockMaxAge\s*=\s*(\d+\s*\*\s*time\.\w+)'),
    "core tools/discovery/network_discovery.go":
        get_anchor(core_disc, r'uploadLockMaxAge\s*=\s*(\d+\s*\*\s*time\.\w+)'),
})


# --- shared secret ----------------------------------------------------------
# Three byte-identical copies. rotate_keys.sh updates the first two; the third
# ships beside IAPTool.exe and is read at runtime, so a board reflashed after a
# rotation stops answering a tool that still holds the old password.
def get_file_hash_or_none(path):
    if not Path(path).exists():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


compare_anchor("iap_fixed_password.txt (3 copies)", {
    "bootloader IAPServer/keys/": get_file_hash_or_none(boot_pwd),
    "core OpenPLC_IAP/src/keys/": get_file_hash_or_none(core_pwd),
    "shipped STM32Tools/*/win/keys/": get_file_hash_or_none(ship_pwd),
})

# --- RTC backup registers ---------------------------------------------------
# Not a mirror but the same failure mode: a shared resource with no allocator.
# Claiming one that the other image already uses cost a real bug (bootloader's
# VBAT witness vs the app's nonce counter, both on DR2, 2026-08-17).
Section("RTC backup register claims")

claims = {}
scan = [
    ("bootloader IAPServer/iap_auth.c", BOOT / "IAPServer" / "iap_auth.c"),
    ("core OpenPLC_IAP/src/iap_auth.c", LIVE / "libraries" / "OpenPLC_IAP" / "src" / "iap_auth.c"),
]
for label, path in scan:
    if not path.exists():
        Warn("SKIP  %s not found" % label)
        skipped += 1
        continue
    text = read_text(path)
    for m in re.finditer(r'RTC_BKP_DR(\d+)', text):
        dr = "DR" + m.group(1)
        claims.setdefault(dr, [])
        if label not in claims[dr]:
            claims[dr].append(label)

for dr in sorted(claims):
    if len(claims[dr]) > 1:
        Fail("CLASH %s claimed by: %s" % (dr, " AND ".join(claims[dr])))
        failed += 1
    else:
        Ok("OK    %s <- %s" % (dr, claims[dr][0]))
print("      (the allocation table in docs/design/ARCHITECTURE.md is the record; this only")
print("       scans the two iap_auth.c files, not the core's backup.h or HID indices)")

# --- what this script does not check ----------------------------------------
Section("not covered by this script -- still manual")
print("  - iap_keyderive HMAC formula across the two C copies and iapcrypto.go")
print("      (covered instead by host/iapcrypto/ and host/bootloader_unit/)")
print("  - fw_pubkey.inc: bootloader-only by design, nothing to compare")
print("  - $CORE_LIVE vs $CORE_REPO: use tools/check-core-sync.ps1")

Section("result")
if failed > 0:
    Fail("%d anchor(s) diverged, %d skipped" % (failed, skipped))
    sys.exit(1)
if skipped > 0:
    Warn("all compared anchors agree, but %d could not be checked" % skipped)
    sys.exit(0)
Ok("all mirrored anchors agree")
sys.exit(0)
