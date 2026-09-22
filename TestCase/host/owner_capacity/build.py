"""Builds and runs the revocation-capacity harness against the real
owner_slot.c in the bootloader. Cases T2-22, T2-23, T1-33 and T2-24.

    python build.py

T2-22 and T2-23 are about what happens as the 'R' segment fills up: the boot line
must start warning with OWNER_REVOKE_LOW_WATER slots left, and the record past
the last one must be refused without writing a byte. On a board that costs all
96 slots permanently -- only a bootloader reflash reclaims them -- so this runs
the same code on a PC instead, over a RAM buffer and a Flash_If_Write() that
enforces the H7's programming rules (stubs/fake_owner_flash.c).

T1-33 is the other end of the same area: owner_slot_compact(), which decides
what survives the sector erase during a flashboot. Getting it wrong costs a
board its ownership with nothing to undo it, and the only way to reach that
function on a board is to actually replace the bootloader.

T2-24 is `setowner --wipe`: the record it will leave behind is judged before
the sector is erased, because an area the board cannot resolve would leave it
unowned with no way back.

stubs/fake_owner_flash.h is passed with -include so it is seen before
owner_slot.h's own (guarded) OWNER_SLOT_BASE.

This is the only host harness that compiles owner_slot.c itself; T1-16 next
door stubs it out, so the write path had no PC coverage before this.

Compiler, first match wins: $CC, then HOST_CC from config/machine.py, then gcc
or clang on PATH. Same order as T1-16 and T2-21 next door.

Exit 0 = every assertion held, non-zero = the compiler or the tests said no.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import EXE, cfg  # noqa: E402

IAP_SERVER = Path(cfg.BOOT_REPO) / "IAPServer"
SHARED_STUBS = HERE.parent / "owner_revoke" / "stubs"


def resolve_cc():
    """$CC, HOST_CC, gcc, clang -- first that exists as a path or on PATH."""
    for cand in (os.environ.get("CC"), getattr(cfg, "HOST_CC", ""), "gcc", "clang"):
        if not cand:
            continue
        if Path(cand).exists():
            return cand
        if shutil.which(cand):
            return cand
    return None


def emit(argv, cwd=None):
    """Run a child, re-emit its merged output verbatim, return its exit code."""
    proc = subprocess.run([str(a) for a in argv], cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.stdout:
        sys.stdout.flush()
        sys.stdout.buffer.write(proc.stdout)
        sys.stdout.buffer.flush()
    return proc.returncode


# The phase groups selfcheck runs as separate steps, so each step id maps to
# the cases it actually covers. Naming a group on the command line runs it
# alone; naming nothing runs every phase in order.
PHASE_GROUPS = {
    "capacity": ("capacity",),
    "compact": ("compact", "compact-verify"),
    "wipe": ("wipe", "wipe-verify"),
    "self-revoke": ("self-revoke",),
}
ALL_PHASES = ("capacity", "compact", "compact-verify", "wipe", "wipe-verify",
              "self-revoke")


def main(argv):
    phases = ALL_PHASES
    if argv:
        if argv[0] not in PHASE_GROUPS:
            print("unknown phase group %s -- expected one of %s"
                  % (argv[0], ", ".join(sorted(PHASE_GROUPS))))
            return 2
        phases = PHASE_GROUPS[argv[0]]

    cc = resolve_cc()
    if not cc:
        print("No C compiler found. Install MinGW-w64 or LLVM/clang and point $HOST_CC "
              "in config/machine.py at its gcc.")
        return 1

    if not IAP_SERVER.is_dir():
        print("Not a directory: %s -- check BOOT_REPO in config/machine.py" % IAP_SERVER)
        return 2

    print("compiler: %s" % cc, flush=True)

    binary = HERE / ("owner_capacity_test" + EXE)
    rc = emit([
        cc, "-std=c11", "-Wall", "-Wextra", "-O0", "-g",
        "-include", HERE / "stubs" / "fake_owner_flash.h",
        "-I", HERE / "stubs",
        "-I", SHARED_STUBS,
        "-I", IAP_SERVER,
        "-I", IAP_SERVER / "uecc",
        HERE / "stubs" / "fake_owner_flash.c",
        # The machine-id stub is shared with T2-21 rather than copied: two
        # copies of the same fake uid would be two things to keep in step.
        SHARED_STUBS / "iap_keyderive_stub.c",
        IAP_SERVER / "owner_slot.c",
        IAP_SERVER / "sha256.c",
        IAP_SERVER / "fw_verify.c",
        IAP_SERVER / "fw_pubkey.c",
        IAP_SERVER / "uecc" / "uECC.c",
        HERE / "test_main.c",
        "-o", binary,
    ])
    if rc != 0:
        return rc

    # One phase per process: owner_slot.c caches its scan and nothing in its
    # public API resets it, so the second arrangement needs a fresh start.
    # Run from HERE -- the harness writes a scratch file next to itself while
    # capturing owner_slot_report()'s output.
    for phase in phases:
        print("===== phase %s" % phase, flush=True)
        rc = emit([binary, phase], cwd=HERE)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
