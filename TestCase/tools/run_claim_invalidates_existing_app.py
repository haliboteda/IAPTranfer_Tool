"""T2-09 -- claiming a board stops the application already on it from booting.

    python tools/run_claim_invalidates_existing_app.py
    python tools/run_claim_invalidates_existing_app.py --key owner.pem
    python tools/run_claim_invalidates_existing_app.py --seconds 12

Case (2)-e of
$PROD/maps/five-paths-e2e-test/issues/E2E-02-what-does-security-mean-per-path.md,
against requirement R2-02. A claimed board verifies firmware against the owner
record in its own flash, so an application installed before the claim -- signed
by the built-in root -- is no longer trusted and the board stays in the
bootloader. Customers will hit this; without a case saying it is by design, the
first person who does reports it as a bug.

⚠️ SOMEBODY HAS TO BE AT THE BOARD. When this script asks for it:

      press RESET and let go, hold BOOT0 while the system LED blinks
      (about 2 s), then LET GO BEFORE 10 SECONDS ARE UP.

   Keep holding past 10 s and the system LED stays lit:
   that is a FACTORY RESET being armed, and letting go then runs it. Press RESET
   again to cancel it. Nothing has to be confirmed afterwards -- the script
   watches the log until the board itself reports it came up with BOOT0 held.

⚠️ PRECONDITIONS, both reported as SETUP (exit 2) and not as FAIL:

     - an application that boots is already installed. "The app stopped
       booting" claims nothing on a board where nothing was booting.
       Install one with: python tools/upload_and_watch.py --bin <file.bin>
     - the board is unclaimed. takeown claims a board that has no owner yet;
       on a claimed board this case does not apply.

⚠️ THIS CLAIMS THE BOARD, and claiming is meant to be hard to undo.

RECOVERY -- reflash the bootloader over ST-Link:

      python tools/flash_bootloader.py

   The owner records live in the bootloader's own sector, so writing the
   bootloader takes them with it, and the application boots again by itself.

The before/after pair is the control: the same board, reset the same way,
booted its application minutes earlier. Both rounds require a proof-of-capture
line, because an empty capture and a silent board look identical.

Exit 0 = the claim invalidated the application, 1 = it did not, 2 = setup.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (LOG_BOOT0_UPLOAD, Fail, Ok, Section, Warn,  # noqa: E402
                    banner, cfg, wait_for_boot0_upload_mode,
                    close_ports, decode_serial, get_programmer_cli,
                    open_log_ports, python_exe, read_log_ports,
                    target_voltage, wait_for_board)

# Start of the application region. $PROD/docs/modules/M1-firmware-upgrade.md,
# "地址布局".
APP_BASE = 0x08020000
APP_PROBE_BYTES = 256

# Printed on every boot whatever the ownership state, so its absence means the
# capture failed rather than the board having nothing to say.
LOG_CAPTURE_PROOF = "Bootloader state:"
# The application was trusted and jumped to.
LOG_APP_RUNS = "** APP Mod"
# The application is present but no longer verifies -- the verdict of this case.
LOG_APP_INVALID = "App signature invalid or absent"
LOG_OWNER_EMPTY = "Owner slot: empty"
LOG_CLAIMED = "Owner slot: claimed at generation"
# What the board prints when the BOOT0 gesture landed.


def read_words(cli, addr, nbytes):
    """Read `nbytes` bytes over SWD as 32-bit words. Returns a list of ints, or None.

    ⚠️ STM32_Programmer_CLI's -r32 length is in BYTES, not words -- passing a
    word count silently reads a quarter of the region.
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


def boot_round(cli, ports, seconds, title):
    """Reset over SWD, capture the boot log, print it. Returns the captured text."""
    Section(title)
    open_ports = open_log_ports(ports)
    subprocess.run([str(cli), "-c", "port=SWD", "mode=UR", "-rst"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    buf = read_log_ports(open_ports, seconds)
    for name, text in buf.items():
        Section("%s  (%d bytes)" % (name, len(text)))
        if text:
            print(text)
    return "\n".join(buf.values())




def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--ip", default="")
    ap.add_argument("--key", default="",
                    help="the owner key to claim with; one is generated when omitted")
    ap.add_argument("--seconds", type=int, default=10,
                    help="how long to listen after each reset")
    ap.add_argument("--boot0-timeout", type=int, default=180,
                    help="how long to wait for the BOOT0 gesture")
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    ports = args.ports if args.ports else cfg.LOG_PORTS
    ip = args.ip or getattr(cfg, "BOARD_IP", "")
    if not ip:
        Fail("need --ip (or set BOARD_IP in config/machine.py)")
        return 2
    cli = get_programmer_cli()

    Section("Target check")
    volts = target_voltage(cli)
    if volts is None:
        Fail("SWD cannot reach the MCU, so nothing here can be read or reset.")
        Warn("  0.00V or no target: board unpowered, or ST-Link VTREF not wired.")
        return 2
    print("target voltage: %.2f V" % volts)

    # ------------------------------------------------- precondition + control
    before = boot_round(cli, ports, args.seconds,
                        "Round 1 of 2: the board before the claim")

    Section("Precondition")
    if LOG_CAPTURE_PROOF not in before:
        Warn("SETUP - no boot log captured (%d bytes); nothing can be judged." % len(before))
        Warn("  Check the log ports listed above.")
        return 2
    Ok("  boot log            captured")

    app_before = read_words(cli, APP_BASE, APP_PROBE_BYTES)
    if app_before is None:
        Fail("SETUP - the application region could not be read over SWD.")
        return 2
    if all(w == 0xFFFFFFFF for w in app_before):
        Warn("SETUP - the application region is blank: there is no application to")
        Warn("  invalidate. Install one first:")
        Warn("    python tools/upload_and_watch.py --bin <file.bin>")
        return 2
    Ok("  application region  written (%d bytes read)" % APP_PROBE_BYTES)

    if LOG_APP_RUNS not in before or LOG_APP_INVALID in before:
        Warn("SETUP - the board did not boot its application, so there is nothing")
        Warn("  for the claim to invalidate. Expected %r in the log above." % LOG_APP_RUNS)
        return 2
    Ok("  application         booted before the claim")

    if LOG_OWNER_EMPTY not in before:
        Warn("SETUP - the board is already claimed; takeown claims an unclaimed board.")
        Warn("  Put it back with: python tools/reset_board_to_factory_state.py")
        return 2
    Ok("  ownership           unclaimed")

    # --------------------------------------------------------------- the claim
    banner(["PRESS RESET, THEN HOLD BOOT0 WHILE THE SYSTEM LED BLINKS.",
            "LET GO BEFORE 10 SECONDS ARE UP."])
    print("  Holding past 10 s arms a FACTORY RESET instead -- the system LED")
    print("  stays lit. Press RESET again to cancel that.")
    print("  Nothing to confirm: this waits for the board to report BOOT0 was held.")
    print()

    seen, text = wait_for_boot0_upload_mode(ports, args.boot0_timeout)
    if text:
        Section("waiting for the BOOT0 gesture")
        print(text)
    if not seen:
        Warn("SETUP - the board never reported %r within %d s."
             % (LOG_BOOT0_UPLOAD, args.boot0_timeout))
        Warn("  Without the gesture takeown is refused, and that refusal would say")
        Warn("  nothing about this case.")
        return 2
    Ok("the board is in upload mode with BOOT0 held")

    if not wait_for_board(ip, timeout=60.0):
        Warn("SETUP - %s never answered discovery, so takeown cannot run." % ip)
        return 2

    Section("Claim  (through tools/run_takeown.py, the case that owns that step)")
    print("  It prints the BOOT0 instruction again -- that part is already done.")
    # --boot0-timeout 0: the gesture was confirmed above, and the board says
    # so only once per boot. Letting run_takeown.py wait for it again waits
    # for a line that has already gone past.
    argv = [python_exe(), str(HERE / "run_takeown.py"), "--ip", ip,
            "--boot0-timeout", "0"]
    if args.key:
        argv += ["--key", args.key]
    rc = subprocess.run(argv).returncode
    if rc != 0:
        Warn("SETUP - the claim did not succeed (exit %d), so what follows would" % rc)
        Warn("  measure a board that was never claimed. T2-01 owns that failure.")
        return 2
    Ok("the board is claimed")

    # ------------------------------------------------------------- the verdict
    after = boot_round(cli, ports, args.seconds,
                       "Round 2 of 2: the same board after the claim")

    Section("T2-09 verdict")
    if LOG_CAPTURE_PROOF not in after:
        Warn("SETUP - no boot log captured after the claim (%d bytes)." % len(after))
        Warn("  A capture that never happened looks exactly like a silent board,")
        Warn("  so this is not a result either way.")
        return 2
    Ok("  boot log            captured")

    checks = []
    for needle, label, want in ((LOG_APP_INVALID, "app refused", True),
                                (LOG_APP_RUNS, "app booted anyway", False),
                                (LOG_CLAIMED, "claim in the log", True)):
        got = needle in after
        checks.append(got == want)
        if got == want:
            Ok("  %-19s %s" % (label, "yes" if want else "no"))
        else:
            Fail("  %-19s %s - expected %s%r"
                 % (label, "yes" if got else "no", "" if want else "no ", needle))

    app_after = read_words(cli, APP_BASE, APP_PROBE_BYTES)
    if app_after is None:
        Warn("  application region  could not be read back")
    elif app_after == app_before:
        Ok("  application region  untouched by the claim (first %d bytes)"
           % APP_PROBE_BYTES)
    else:
        # Withdrawing trust is not supposed to write anything.
        Warn("  application region  CHANGED during the claim (first %d bytes)"
             % APP_PROBE_BYTES)

    print("")
    if all(checks):
        Ok("PASS - the application booted before the claim and is refused after it.")
        Ok("       Same board, same reset: the earlier boot is the control.")
        print("       To get the application back: python tools/flash_bootloader.py")
        return 0
    Fail("FAIL - claiming did not invalidate the application that was installed.")
    Fail("       Read both boot logs above: the difference between them is the case.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
