"""Shared by everything under tools/. Import it first:

    from common import cfg, Section, Ok, Warn, Fail, get_go_bin, ...

This is the Python side of M7 (see open_plc_cube_ide/docs/handover/Todo/
M7-python-scripts.md). It is a translation of tools/_common.ps1 and must behave
identically to it -- the PowerShell version stays until every case has been
shown to reach the same verdict through both.

Run it directly to see what this machine resolves to:

    python tools/common.py --probe
"""

import glob
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- platform
# sys.platform says "win32" even on 64-bit Windows, and "darwin" for macOS.
# Normalise once, here, so nothing below ever tests sys.platform again.
if sys.platform.startswith("win"):
    PLATFORM = "windows"
elif sys.platform == "darwin":
    PLATFORM = "macos"
else:
    PLATFORM = "linux"

IS_WIN = PLATFORM == "windows"
EXE = ".exe" if IS_WIN else ""

# Three tool families, three different names for the same three platforms.
# Keeping the mapping here is the whole point: no script below spells any of
# them out.
GOOS_DIR = {"windows": "windows", "linux": "linux", "macos": "darwin"}[PLATFORM]     # compile_tool.sh output layout
A15_DIR = {"windows": "win", "linux": "linux", "macos": "macosx"}[PLATFORM]          # Arduino15 packages/*/tools/STM32Tools/*/
CUBE_PLUG = {"windows": "win32", "linux": "linux64", "macos": "macos64"}[PLATFORM]   # CubeIDE externaltools plugin suffix


# ---------------------------------------------------------------- config
def _load_machine():
    """Import config/machine.py, the one file that differs between machines."""
    path = Path(__file__).resolve().parent.parent / "config" / "machine.py"
    if not path.exists():
        print("config/machine.py is missing.", file=sys.stderr)
        print("Copy config/machine.example.py to config/machine.py and fill in "
              "this machine's paths.", file=sys.stderr)
        sys.exit(1)
    import importlib.util
    spec = importlib.util.spec_from_file_location("machine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cfg = _load_machine()


# ---------------------------------------------------------------- output
# ANSI colours, matching the PowerShell version's Cyan/Green/Yellow/Red. Windows
# consoles only understand them once virtual terminal processing is on, which
# python does not enable for us; NO_COLOR turns them off everywhere.
def _colour_ok():
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


_COLOUR = _colour_ok()


def _paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if _COLOUR else text


def Section(t): print(); print(_paint("===== " + t, "36"))
def Ok(t):      print(_paint(t, "32"))
def Warn(t):    print(_paint(t, "33"))
def Fail(t):    print(_paint(t, "31"))


# ---------------------------------------------------------------- paths
def get_scratch_dir():
    """Scratch files (redirected stdout, oversized test images, phase-1 state).

    tempfile honours TMPDIR/TEMP/TMP and falls back to /tmp, so unlike the
    PowerShell version there is no platform test to get wrong here.
    """
    return Path(tempfile.gettempdir())


def get_scratch_file(name):
    return get_scratch_dir() / name


def get_go_bin(name):
    """Where compile_tool.sh and `go build -o Output/...` put binaries for THIS host."""
    return Path(cfg.TOOL_REPO) / "Output" / GOOS_DIR / (name + EXE)


def _newest(pattern):
    hits = sorted(glob.glob(str(pattern)))
    return Path(hits[-1]) if hits else None


def get_iap_tool():
    """IAPTool ships inside the Arduino board package, one directory per platform.

    Its version is independent of the core's, so resolve both by wildcard: a
    package update must not require editing config/.
    """
    override = getattr(cfg, "IAPTOOL", "")
    if override and Path(override).exists():
        return Path(override)
    a15 = getattr(cfg, "A15", "")
    if not a15:
        Fail("IAPTOOL not set and A15 not set in config/machine.py")
        sys.exit(1)
    hit = _newest(Path(a15) / "packages" / "OpenPLC_Alpha" / "tools" / "STM32Tools"
                  / "*" / A15_DIR / ("IAPTool" + EXE))
    if not hit:
        Fail("IAPTool not found under %s for platform '%s' "
             "(looked in .../STM32Tools/*/%s/)" % (a15, PLATFORM, A15_DIR))
        sys.exit(1)
    return hit


def get_programmer_cli():
    """STM32_Programmer_CLI lives in a versioned plugin directory, so resolve it
    by wildcard and take the newest. Hardcoding the version breaks on every
    CubeIDE update -- which is the class of thing config/ exists to avoid.

    The non-Windows plugin suffixes are ST's documented naming and are NOT
    verified; if the lookup fails on Linux, the printed glob is the thing to
    compare against the real install.
    """
    pattern = (Path(cfg.CUBEIDE) / "STM32CubeIDE" / "plugins"
               / ("com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.%s_*" % CUBE_PLUG)
               / "tools" / "bin" / ("STM32_Programmer_CLI" + EXE))
    hit = _newest(pattern)
    if not hit:
        Fail("STM32_Programmer_CLI not found. Looked for: %s" % pattern)
        sys.exit(1)
    return hit


def get_cube_ide_exe():
    """The headless launcher, whose name differs per platform."""
    name = "stm32cubeidec.exe" if IS_WIN else "stm32cubeide"
    exe = Path(cfg.CUBEIDE) / "STM32CubeIDE" / name
    if not exe.exists():
        Fail("%s not found at %s" % (name, exe))
        sys.exit(1)
    return exe


# ---------------------------------------------------------------- serial
def _import_serial():
    """pyserial is the one dependency that is not in the standard library.

    Report it by name rather than dying on an ImportError traceback: a missing
    optional dependency must read as "install this", not as a crash.
    """
    try:
        import serial  # noqa: F401
        return serial
    except ImportError:
        Fail("pyserial is not installed -- serial capture is unavailable.")
        Warn("  pip install pyserial")
        if not IS_WIN:
            Warn("  on Debian:  apt install python3-serial   (or use a venv)")
            Warn("  and make sure the user is in the dialout group for /dev/tty* access.")
        return None


def get_port_holder_hint():
    """Which process is holding a serial port. Neither OS offers a direct answer,
    so this is a best-effort name match against the usual terminals -- enough to
    say "close sscom" instead of "access denied".
    """
    known = ("sscom", "putty", "xshell", "securecrt", "mobaxterm", "ttermpro", "teraterm",
             "realterm", "termite", "hterm", "accessport", "xcom", "uartassist", "arduino",
             "minicom", "picocom", "screen", "cu", "tio")
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                                 capture_output=True, text=True, timeout=10).stdout
            names = [line.split(",")[0].strip('"') for line in out.splitlines() if line]
        else:
            out = subprocess.run(["ps", "-eo", "comm="],
                                 capture_output=True, text=True, timeout=10).stdout
            names = out.split()
    except Exception:
        return None
    hits = sorted({n for n in names for k in known if k in n.lower()})
    return ", ".join(hits) if hits else None


def open_log_ports(ports):
    """Open every port that can be opened; report the ones that cannot and name
    the likely culprit. Returns a dict of name -> Serial.
    """
    serial = _import_serial()
    if serial is None:
        return {}
    open_ports = {}
    for p in ports:
        if not p:
            continue
        try:
            h = serial.Serial(p, cfg.LOG_BAUD, timeout=0.3)
            open_ports[p] = h
            print("listening on %s @ %d" % (p, cfg.LOG_BAUD))
        except Exception as e:
            msg = str(e)
            Warn("cannot open %s: %s" % (p, msg))
            if re.search(r"denied|Permission", msg, re.I):
                who = get_port_holder_hint()
                if who:
                    Warn("  a serial terminal is holding it: %s -- close it and retry" % who)
                if not IS_WIN:
                    Warn("  or the user is not in the dialout group")
    return open_ports


def read_log_ports(open_ports, seconds):
    """Drain the given ports for `seconds` and return name -> captured text."""
    import time
    buf = {k: "" for k in open_ports}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for k, h in open_ports.items():
            try:
                n = h.in_waiting
                if n:
                    buf[k] += h.read(n).decode("utf-8", "replace")
            except Exception:
                pass
        time.sleep(0.1)
    for h in open_ports.values():
        try:
            h.close()
        except Exception:
            pass
    return buf


# ---------------------------------------------------------------- board
def assert_target_reachable(cli):
    """Refuses to go further when SWD cannot reach the MCU, and says why. A target
    voltage of 0.00V means the board is unpowered or ST-Link VTREF is not wired --
    VTREF being the one people forget while the other three lines are all correct.
    """
    probe = subprocess.run([str(cli), "-c", "port=SWD", "mode=HOTPLUG"],
                           capture_output=True, text=True).stdout
    m = re.search(r"Voltage\s*:\s*(.+)$", probe, re.M)
    volt = m.group(1).strip() if m else "unknown"
    print("target voltage: %s" % volt)
    if "No STM32 target found" in probe:
        Fail("SWD cannot reach the MCU.")
        if volt.startswith("0.00"):
            Warn("  0.00V -> board unpowered, or ST-Link VTREF/VDD not wired.")
        if not IS_WIN:
            Warn("  on Linux this is also what a missing ST-Link udev rule looks like.")
        sys.exit(1)


# ---------------------------------------------------------------- probe
def probe(verbose=True):
    """What this machine actually has. This is selfcheck's step A0.

    Nothing here fails the run: CubeIDE and a serial port are needed to reach the
    board, not to pass the host-side checks.

    Returns the list of missing things, by label.
    """
    if verbose:
        Section("A0  this machine")
        print("  %-19s %s   (Python %d.%d.%d)" % (
            "platform", PLATFORM,
            sys.version_info[0], sys.version_info[1], sys.version_info[2]))

    missing = []

    def show(label, path, why):
        if path and Path(path).exists():
            if verbose:
                print("  %-19s %s" % (label, path))
        else:
            if verbose:
                Warn("  %-19s MISSING - %s" % (label, why))
            missing.append(label)

    def show_cmd(label, cmd, why):
        from shutil import which
        found = which(cmd)
        # which() returns the extension cased as PATHEXT spells it, which is
        # upper case by default -- so this would print go.EXE where the
        # PowerShell version prints go.exe. M7 verifies by diffing the two
        # outputs; a cosmetic difference teaches people to ignore diffs.
        if found and IS_WIN:
            p = Path(found)
            found = str(p.with_suffix(p.suffix.lower()))
        if found:
            if verbose:
                print("  %-19s %s" % (label, found))
        else:
            if verbose:
                Warn("  %-19s MISSING - %s" % (label, why))
            missing.append(label)

    show("BOOT_REPO", cfg.BOOT_REPO, "bootloader repo; set it in config/machine.py")
    show("CORE_REPO", cfg.CORE_REPO, "Arduino core repo; set it in config/machine.py")
    show("TOOL_REPO", cfg.TOOL_REPO, "this repo; set it in config/machine.py")
    show("CORE_LIVE", cfg.CORE_LIVE, "install the board package in the Arduino IDE first")
    show_cmd("go", "go", "A1/A3 and every IAPTool build need it")
    show_cmd("python", "python3" if not IS_WIN else "python", "A10/A11/A12 need it")
    show("arduino-cli", cfg.ARDUINO_CLI, "A13 and command-line app builds need it")
    show("CubeIDE", cfg.CUBEIDE, "needed to build and flash the bootloader, not for the checks below")

    # The programmer and IAPTool are resolved by wildcard, so report what the
    # lookup found rather than what config says -- that is the value the scripts
    # will use.
    hit = _newest(Path(cfg.CUBEIDE) / "STM32CubeIDE" / "plugins"
                  / ("com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.%s_*" % CUBE_PLUG)
                  / "tools" / "bin" / ("STM32_Programmer_CLI" + EXE))
    if hit:
        if verbose:
            print("  %-19s %s" % ("programmer CLI", hit))
    else:
        if verbose:
            Warn("  %-19s MISSING - no '%s' plugin under CubeIDE" % ("programmer CLI", CUBE_PLUG))
        missing.append("programmer CLI")

    hit = _newest(Path(getattr(cfg, "A15", "")) / "packages" / "OpenPLC_Alpha" / "tools"
                  / "STM32Tools" / "*" / A15_DIR / ("IAPTool" + EXE)) if getattr(cfg, "A15", "") else None
    if hit:
        if verbose:
            print("  %-19s %s" % ("shipped IAPTool", hit))
    else:
        if verbose:
            Warn("  %-19s MISSING - no IAPTool for '%s' under the board package"
                 % ("shipped IAPTool", A15_DIR))
        missing.append("shipped IAPTool")

    if verbose:
        print("  %-19s %s" % ("log ports (config)", ", ".join(cfg.LOG_PORTS)))
        for p in cfg.LOG_PORTS:
            if not IS_WIN and p and not Path(p).exists():
                Warn("  %-19s %s does not exist -- check the adapter and the dialout group" % ("", p))

    if verbose:
        if missing:
            Warn("  -> %d thing(s) missing on this machine: %s" % (len(missing), ", ".join(missing)))
            Warn("     see open_plc_cube_ide/CLAUDE.md for what each one is and where to get it")
        else:
            Ok("  everything config points at exists")
    return missing


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
        sys.exit(0)
    print(__doc__)
