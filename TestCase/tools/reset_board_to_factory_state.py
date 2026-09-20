"""Put a board back into the state it leaves the factory in, and prove it got there.

    python tools/reset_board_to_factory_state.py              erase, flash, verify
    python tools/reset_board_to_factory_state.py --check-only verify only; never writes
    python tools/reset_board_to_factory_state.py --seconds 20 watch the boot log longer

Factory state is "ST-Link has written the bootloader onto an otherwise blank
chip": owner record area erased, no application, no journal history. Every
end-to-end path starts here, so a path's result means nothing unless the
starting point is known -- which is why this refuses to report PASS on log
evidence alone.

⚠️ THIS ERASES THE WHOLE CHIP. Ownership, application and journal are all gone.
A claimed board has to be claimed again afterwards.

The verdict needs BOTH kinds of evidence, because either alone can lie:

  flash reads  -- read back over SWD; independent of the serial port entirely
  boot log     -- what the bootloader says about itself

An empty capture is INCONCLUSIVE, never PASS: a log that was never captured
looks exactly like a log that said nothing.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, assert_target_reachable,  # noqa: E402
                    cfg, get_programmer_cli, open_log_ports, read_log_ports)

# Where the three things that must be blank live. Addresses are the ones in
# $PROD/docs/modules/M1-firmware-upgrade.md, "地址布局".
OWNER_SLOT_BASE = 0x0801E000
OWNER_SLOT_SIZE = 8 * 1024
APP_BASE = 0x08020000
JOURNAL_BASE = 0x081E0000

# Two lines the bootloader prints on an unclaimed board. The second one is the
# only way a customer ever learns the board is undefended, so its absence is a
# finding in its own right -- see the T2-06 case.
LOG_OWNER_EMPTY = "Owner slot: empty"
LOG_PUBLIC_ROOT = "trusts the PUBLISHED root key"
# A freshly erased journal has nothing in it and no metadata record.
LOG_JOURNAL_EMPTY = "0/4096 journal slots used"
# Proves the capture worked at all. NOT the SDRAM self-test line: that only
# prints when the board stays in the bootloader, so on a board that still has
# an application its absence means "not reached", not "nothing captured".
LOG_CAPTURE_PROOF = "Bootloader state:"


def read_words(cli, addr, nbytes):
    """Read `nbytes` bytes over SWD as 32-bit words. Returns a list of ints, or None.

    ⚠️ STM32_Programmer_CLI's -r32 length is in BYTES, not words -- passing a
    word count silently reads a quarter of the region and reports it blank.
    """
    out = subprocess.run(
        [str(cli), "-c", "port=SWD", "mode=HOTPLUG", "-r32", hex(addr), hex(nbytes)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace").stdout or ""
    words = []
    for line in out.splitlines():
        m = re.match(r"^0x[0-9A-Fa-f]{8}\s*:\s*(.*)$", line.strip())
        if m:
            words.extend(int(w, 16) for w in m.group(1).split())
    return words if words else None


def region_is_blank(cli, addr, nbytes, what):
    """True when every word reads back as erased flash."""
    got = read_words(cli, addr, nbytes)
    if got is None:
        Fail("  %-22s could not be read over SWD" % what)
        return False
    bad = [(i, w) for i, w in enumerate(got) if w != 0xFFFFFFFF]
    if bad:
        i, w = bad[0]
        Fail("  %-22s NOT blank: %d of %d words written, first at +0x%X = 0x%08X"
             % (what, len(bad), len(got), i * 4, w))
        return False
    Ok("  %-22s blank (%d words all 0xFFFFFFFF)" % (what, len(got)))
    return True


def snapshot(cli, title):
    """Report what is currently in the three regions. Returns (owner, app, journal)
    blankness, so the caller can show what the erase actually changed."""
    Section(title)
    owner = region_is_blank(cli, OWNER_SLOT_BASE, OWNER_SLOT_SIZE, "owner record area")
    app = region_is_blank(cli, APP_BASE, 256, "application region")
    journal = region_is_blank(cli, JOURNAL_BASE, 256, "journal sector")
    return owner, app, journal


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--check-only", action="store_true",
                    help="verify the current state; never erase or flash")
    ap.add_argument("--seconds", type=int, default=12)
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    ports = args.ports if args.ports else cfg.LOG_PORTS
    elf = Path(cfg.BOOT_REPO) / "Debug" / "open_plc_cube_ide.elf"
    cli = get_programmer_cli()

    Section("Target check")
    assert_target_reachable(cli)

    before = snapshot(cli, "Before" if not args.check_only else "Current contents")

    if not args.check_only:
        if not elf.exists():
            Fail("no bootloader .elf at %s" % elf)
            Fail("  build it first: python tools/flash_bootloader.py")
            return 1

        Section("Mass erase")
        Warn("erasing the whole chip - ownership, application and journal are going away")
        out = subprocess.run([str(cli), "-c", "port=SWD", "mode=UR", "-e", "all"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace").stdout or ""
        for line in out.splitlines():
            if re.search(r"Erasing|erased|Error", line):
                print(line)
        if re.search(r"Error", out):
            Fail("the erase reported an error - stopping before flashing")
            return 1

        # Erase before flash, checked separately: a bootloader written on top of
        # a failed erase still boots and still prints the right lines, so the log
        # cannot tell the two apart. This is the only moment the difference is
        # visible.
        Section("After erase, before flashing")
        if not all(snapshot(cli, "Erase check")):
            Fail("the chip is not blank after a mass erase - stopping")
            return 1

        Section("Flash bootloader")
        out = subprocess.run(
            [str(cli), "-c", "port=SWD", "mode=UR", "-w", str(elf), "-rst"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace").stdout or ""
        for line in out.splitlines():
            if re.search(r"Download|verified|Error|Reset", line):
                print(line)
        if re.search(r"Error", out):
            Fail("the download reported an error")
            return 1

    Section("Reset + capture boot log")
    open_ports = open_log_ports(ports)
    subprocess.run([str(cli), "-c", "port=SWD", "mode=UR", "-rst"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    buf = read_log_ports(open_ports, args.seconds)
    log = "\n".join(buf.values())
    for k, v in buf.items():
        Section("%s  (%d bytes)" % (k, len(v)))
        if v:
            print(v)

    # ---------------------------------------------------------------- verdict
    Section("Factory-state verdict")

    owner_blank = region_is_blank(cli, OWNER_SLOT_BASE, OWNER_SLOT_SIZE,
                                  "owner record area")
    app_blank = region_is_blank(cli, APP_BASE, 256, "application region")

    print("")
    captured = LOG_CAPTURE_PROOF in log
    if not captured:
        Warn("  boot log            NOT captured (%d bytes)" % len(log))
    else:
        Ok("  boot log            captured")
        for needle, label in ((LOG_OWNER_EMPTY, "owner slot empty"),
                              (LOG_PUBLIC_ROOT, "published-root warning"),
                              (LOG_JOURNAL_EMPTY, "journal empty")):
            if needle in log:
                Ok("  %-19s yes" % label)
            else:
                Fail("  %-19s NO - expected %r" % (label, needle))

    print("")
    flash_ok = owner_blank and app_blank
    log_ok = (captured and LOG_OWNER_EMPTY in log
              and LOG_PUBLIC_ROOT in log and LOG_JOURNAL_EMPTY in log)

    if flash_ok and log_ok:
        Ok("PASS - factory state, confirmed by flash reads AND the boot log.")
        print("       Before this run: owner %s, app %s, journal %s."
              % tuple("blank" if b else "written" for b in before))
        return 0
    if flash_ok and not captured:
        Warn("INCONCLUSIVE - flash reads say factory state, but no boot log arrived.")
        Warn("  A log that was never captured looks exactly like a log that said")
        Warn("  nothing, so this is not a PASS. Check the log ports listed above.")
        return 2
    Fail("FAIL - this board is NOT in factory state.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
