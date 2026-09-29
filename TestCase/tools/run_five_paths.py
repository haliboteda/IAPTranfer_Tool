"""The five user paths, end to end, from factory state.

    python3 tools/run_five_paths.py                    the whole round
    python3 tools/run_five_paths.py --from 3           resume at path 3
    python3 tools/run_five_paths.py --only 1 2         just these
    python3 tools/run_five_paths.py --dry-run          print the plan, touch nothing

Every case in this round can already be run on its own. What has never been
run is the round: the paths share state, and the order is part of what is
under test -- path 4 issues the leaf that path 5 revokes, and path 2's "the
old application stopped working" only means anything because path 1 put one
there. Running them separately proves each step and nothing about the story.

Plan and criteria: $PROD/docs/engineering/HOW-TO-RUN-TESTS.md, section
"五条用户路径 · 从出厂态跑一整轮". This script is that section, executable.

⚠️ YOU HAVE TO BE AT THE BOARD TWICE, both times to hold BOOT0: once in path 2
(the first claim) and once in path 5 (re-claiming after path 3 erased the
owner records). The script waits for the board itself to report the gesture,
so there is nothing to type -- but it will sit there until you do it.

⚠️ DESTRUCTIVE. It mass-erases the board, rotates the root compiled into the
bootloader, and claims the board three times over. Do not point it at
anything you are not finished with.

Three sequencing traps, all of them the reason a naive run of the order table
does not work. They are handled here, and named so the next person does not
re-discover them:

  * `inject_owner_record.py` (step 2-d) writes its record by REFLASHING the
    bootloader, which erases the owner area -- including the claim step 2-b
    just made. It is given the same root, so the board comes back claimed by
    the same key and no second BOOT0 press is needed.
  * `run_claim_invalidates_existing_app.py` (step 2-e) performs its own
    takeown. Running step 2-b separately would claim the board twice, so 2-b
    and 2-e are one invocation here.
  * `run_rotate_root_revokes_old_leaf.py` (steps 5-c..5-e) performs its own
    setowner, which IS step 5-b. Running run_setowner.py first would spend a
    generation and hand the board to a key the later script does not know
    about. Only the negative half of 5-a/b is run separately.

Exit code is the verdict: 0 every path held, 1 something failed, 2 a
precondition was never reached (nothing was proven either way).
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, banner, cfg,  # noqa: E402
                    get_iap_tool, get_programmer_cli, get_scratch_file,
                    python_exe, run_capture, run_while_draining)

PASS, FAIL, SETUP = "PASS", "FAIL", "SETUP"

# What rotate_keys.sh leaves behind. The published key it replaced is renamed
# to .TEST_ONLY.pem.bak, so anything still pointing at the old name after
# path 3 is pointing at a file that no longer exists -- which is how path 4
# failed the first time this round was run.
ROTATED_ROOT_KEY = "IAPServer/keys/fw_signing_key.pem"
PUBLISHED_ROOT_KEY = "IAPServer/keys/fw_signing_key.TEST_ONLY.pem"


class Round(object):
    """Runs the steps and remembers what each one left behind."""

    def __init__(self, args):
        self.args = args
        self.ip = args.ip or cfg.BOARD_IP
        self.results = []
        self.keys = {}          # name -> .pem path
        self.keydir = Path(args.keydir)

    # ---------------------------------------------------------------- plumbing
    def tool(self, *argv):
        """A tools/ script, as a child, with its output passed through."""
        cmd = [python_exe(), str(HERE / argv[0])] + [str(a) for a in argv[1:]]
        print("    $ %s" % " ".join(Path(c).name if c.endswith(".py") else c
                                    for c in cmd[1:]))
        if self.args.dry_run:
            return 0
        return subprocess.run(cmd).returncode

    def record(self, step, verdict, note=""):
        self.results.append((step, verdict, note))
        {PASS: Ok, FAIL: Fail, SETUP: Warn}[verdict]("  %s %s %s" % (step, verdict, note))
        return verdict == PASS

    def genkey(self, name):
        """A fresh key, kept where the operator can find it afterwards."""
        where = self.keydir / name
        where.mkdir(parents=True, exist_ok=True)
        if not self.args.dry_run:
            run_capture([str(get_iap_tool()), "genkey", name], cwd=where)
        pem = where / ("%s.pem" % name)
        self.keys[name] = pem
        print("    key %s -> %s" % (name, pem))
        return pem

    def ensure_bootloader(self, *candidates):
        """Get the board out of its application and into the bootloader.

        Every command this round sends to the bootloader -- takeown, setowner,
        getpubkey -- is answered only by the bootloader, and a board that just
        finished an upload is running the application instead. Without this a
        step "fails" having never reached the board, which proves nothing and
        reads exactly like the board misbehaving.

        Several keys are tried because which one works is not obvious and
        changes as the round goes on. The application checks the reboot
        request against whatever owner_root_ro() resolves to: the owner root
        once the board is claimed, and otherwise the root compiled into THAT
        APPLICATION -- which is the root that was current when it was built,
        not necessarily the one the bootloader trusts now. After path 3 those
        are two different keys.
        """
        if self.args.dry_run:
            print("    $ enter_bootloader.py (if an application is running)")
            return True
        for key in candidates:
            if key and Path(key).exists():
                if self.tool("enter_bootloader.py", "--key", key, "--seconds", "6") == 0:
                    print("    (the application accepted %s)" % Path(key).name)
                    return True
        Warn("  no key could ask the application to step aside")
        return False

    def boot0_is_held(self):
        """Did the board come up with BOOT0 held?

        Step 2-a needs it NOT held and step 2-b needs it held, in that order.
        An operator who presses when they are first told to has pressed before
        2-a runs, and 2-a then claims the board instead of being refused --
        a green light on the wrong thing. Ask the board rather than assume.
        """
        if self.args.dry_run:
            return False
        from common import LOG_BOOT0_UPLOAD, close_ports, open_log_ports
        ports = list(self.args.ports if getattr(self.args, "ports", None)
                     else cfg.LOG_PORTS)
        handles = open_log_ports(ports)
        _, buf = run_while_draining(
            [str(get_programmer_cli()), "-c", "port=SWD", "mode=UR", "-rst"],
            handles, get_scratch_file("boot0_probe.out"),
            get_scratch_file("boot0_probe.err"), tail_seconds=6)
        close_ports(handles)
        return LOG_BOOT0_UPLOAD in "\n".join(buf.values())

    def hold_boot0(self, why):
        banner(["HOLD BOOT0 NOW: press RESET, hold BOOT0 while the system",
                "LED blinks (about 2 s), then let go.", why])


def path_0(r):
    Section("0 · factory state")
    Warn("  this mass-erases the board and reflashes the bootloader")
    rc = r.tool("reset_board_to_factory_state.py")
    if rc == 2:
        return r.record("0", SETUP, "factory state could not be proven")
    return r.record("0", PASS if rc == 0 else FAIL)


def path_1(r):
    Section("1 · burn your own program on an untouched board, then upgrade")
    published = Path(cfg.BOOT_REPO) / PUBLISHED_ROOT_KEY

    ok = True
    ok &= r.record("1-install", PASS if r.tool(
        "upload_and_watch.py", "--bin", r.args.v1, "--key", published,
        "--expect-banner", "IAP_PROBE_APP up v1") == 0 else FAIL)
    ok &= r.record("1-upgrade", PASS if r.tool(
        "upload_and_watch.py", "--bin", r.args.v2, "--key", published,
        "--expect-banner", "IAP_PROBE_APP up v2") == 0 else FAIL,
        "the banner changed, so something really was replaced")
    ok &= r.record("1-a/T2-08", PASS if r.tool(
        "run_public_root_warning_is_persistent.py") == 0 else FAIL,
        "the warning is on every boot, not just the first")
    return ok


def path_2(r):
    Section("2 · claim the board, then burn and upgrade")
    ok = True

    # Path 1 left an application running, and it owns the port the bootloader
    # would answer on. The board is still unclaimed here, so its owner area is
    # empty and the application falls back to the published root -- which is
    # therefore the key that can ask it to step aside.
    published = Path(cfg.BOOT_REPO) / PUBLISHED_ROOT_KEY
    if not r.ensure_bootloader(published, Path(cfg.BOOT_REPO) / ROTATED_ROOT_KEY):
        return r.record("2-a/T2-02", SETUP,
                        "could not reach the bootloader; nothing was attempted")

    # 2-a is the negative case: it needs BOOT0 NOT held. Asking the operator
    # to press before this point turns it into a successful claim, which is
    # a failure of this case reported as if the board had misbehaved.
    if r.boot0_is_held():
        return r.record("2-a/T2-02", SETUP,
                        "BOOT0 is held right now; 2-a needs it released. "
                        "Reset without touching it and run this path again.")

    ok &= r.record("2-a/T2-02", PASS if r.tool(
        "run_takeown.py", "--expect-refused") == 0 else FAIL,
        "no BOOT0, no claim")

    # The key is generated HERE and handed to the claim script, not left for
    # it to generate: everything after path 2 has to sign with the same key,
    # and a key the round never learns about ends the round.
    owner = Path(r.args.owner_key) if r.args.owner_key else r.genkey("owner_after_claim")

    r.hold_boot0("Path 2 claims the board for the first time.")
    # 2-b and 2-e together: this script claims the board itself, so running
    # run_takeown.py first would claim it twice.
    ok &= r.record("2-b+2-e/T2-01+T2-09", PASS if r.tool(
        "run_claim_invalidates_existing_app.py", "--key", owner,
        "--boot0-timeout", r.args.boot0_timeout) == 0 else FAIL,
        "claimed, and path 1's application stopped being accepted")

    ok &= r.record("2-e-upload", PASS if r.tool(
        "upload_and_watch.py", "--bin", r.args.v1, "--key", owner,
        "--expect-banner", "IAP_PROBE_APP up v1") == 0 else FAIL,
        "an application signed by the new owner runs again")
    r.keys["owner"] = owner
    return ok


def path_3(r):
    Section("3 · compile your own root into the bootloader, then upgrade")
    Warn("  this rewrites $BOOT/IAPServer/keys and erases the owner records")
    if r.args.dry_run:
        print("    $ rotate_keys.sh --yes ; build_image.py ; flash_bootloader.py --skip-build")
    else:
        rot = Path(cfg.BOOT_REPO) / "IAPServer" / "keys" / "rotate_keys.sh"
        rc = subprocess.run([cfg.GIT_BASH, str(rot), "--yes"]).returncode
        if rc != 0:
            return r.record("3-rotate", SETUP, "rotate_keys.sh failed")
        if r.tool("build_image.py") != 0:
            return r.record("3-build", SETUP, "the rebuild failed")
        if r.tool("flash_bootloader.py", "--skip-build") != 0:
            return r.record("3-flash", SETUP, "flashing failed")

    return r.record("3-a/T2-07", PASS if r.tool(
        "run_custom_root_has_no_warning.py",
        "--bin", r.args.v2, "--expect-banner", "IAP_PROBE_APP up v2") == 0 else FAIL,
        "own root, no warning, still takes firmware")


def path_4(r):
    Section("4 · issue a leaf certificate, then upgrade")
    # The root to issue from is the one path 3 rotated in, NOT the published
    # key -- rotate_keys.sh renamed that one out of the way, so the default
    # here points at a file that is gone.
    root = Path(cfg.BOOT_REPO) / ROTATED_ROOT_KEY
    if not root.exists():
        return r.record("4-c/T2-11", SETUP,
                        "no rotated root key at %s -- did path 3 run?" % root)

    # Path 3 ended with an upload, so an application is running and holding
    # the port. It was built before the rotation, so the key it trusts is the
    # published one -- which rotate_keys.sh has renamed out of the way.
    if not r.ensure_bootloader(root,
                               Path(cfg.BOOT_REPO) / (PUBLISHED_ROOT_KEY + ".bak"),
                               Path(cfg.BOOT_REPO) / PUBLISHED_ROOT_KEY):
        return r.record("4-c/T2-11", SETUP,
                        "could not reach the bootloader; nothing was attempted")

    return r.record("4-c/T2-11", PASS if r.tool(
        "run_delegated_cert_on_real_board.py", "--bin", r.args.v1,
        "--root-key", root) == 0 else FAIL,
        "a colleague uploaded with a leaf the board's root issued")


def path_5(r):
    Section("5 · revoke / replace a leaf, then upgrade")
    r.hold_boot0("Path 3 erased the owner records, and setowner needs a claimed board.")
    owner2 = r.genkey("owner_for_path5")
    ok = r.record("5-0/takeown", PASS if r.tool(
        "run_takeown.py", "--key", owner2,
        "--boot0-timeout", r.args.boot0_timeout) == 0 else FAIL, "re-claimed")
    if not ok:
        return False

    ok &= r.record("5-a/T2-03-", PASS if r.tool(
        "run_setowner.py", "--current-key", owner2, "--bad-signature") == 0 else FAIL,
        "a bad signature changes nothing")

    # 5-b is the rotation this script performs itself; running run_setowner.py
    # for it as well would spend a generation the script below does not expect.
    ok &= r.record("5-b..5-e/T2-12..T2-14", PASS if r.tool(
        "run_rotate_root_revokes_old_leaf.py",
        "--bin", r.args.v1, "--current-key", owner2) == 0 else FAIL,
        "changing the root retired the old leaf, and a new one works")
    return ok


PATHS = {0: path_0, 1: path_1, 2: path_2, 3: path_3, 4: path_4, 5: path_5}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="")
    ap.add_argument("--v1", default="../Output/probe-images/iap_probe_v1.bin")
    ap.add_argument("--v2", default="../Output/probe-images/iap_probe_v2.bin")
    ap.add_argument("--owner-key", default="",
                    help="skip generating one and use this for path 2 onwards")
    ap.add_argument("--keydir", default="../Output/five-paths-keys")
    ap.add_argument("--from", dest="start", type=int, default=0,
                    help="resume at this path; 0 is factory state")
    ap.add_argument("--only", nargs="*", type=int, default=None)
    ap.add_argument("--boot0-timeout", type=int, default=600,
                    help="how long each BOOT0 step waits for you. Generous on "
                         "purpose: the countdown starts when the step is "
                         "reached, which is before anybody has read the "
                         "message telling them to press anything.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would run and touch nothing")
    args = ap.parse_args()

    wanted = sorted(args.only) if args.only is not None else \
        [n for n in sorted(PATHS) if n >= args.start]

    Section("the round")
    print("  board  %s" % (args.ip or cfg.BOARD_IP))
    print("  paths  %s" % ", ".join(str(n) for n in wanted))
    print("  images %s -> %s" % (args.v1, args.v2))
    if args.dry_run:
        Warn("  dry run: nothing below touches the board")
    presses = sum(1 for n in wanted if n in (2, 5))
    if presses:
        Warn("  you will be asked to hold BOOT0 %d time(s)" % presses)

    r = Round(args)
    for n in wanted:
        if not PATHS[n](r) and not args.dry_run:
            Warn("  path %d did not hold; the rest would run on a board in an "
                 "unknown state, so stopping here" % n)
            break

    Section("verdict")
    for step, v, note in r.results:
        print("  %-24s %-6s %s" % (step, v, note))
    bad = [v for _, v, _ in r.results if v == FAIL]
    setup = [v for _, v, _ in r.results if v == SETUP]
    if bad:
        Fail("%d step(s) failed" % len(bad))
        return 1
    if setup:
        Warn("%d step(s) never reached their precondition" % len(setup))
        return 2
    Ok("the whole round held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
