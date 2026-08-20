"""Work out what this machine has, and write config/machine.py and machine.ps1.

    python3 tools/init_machine.py                    detect, ask for the rest, write
    python3 tools/init_machine.py --check            look, change nothing, ask nothing
    python3 tools/init_machine.py --no-input         detect only; report what is missing
    python3 tools/init_machine.py --set CUBEIDE=/opt/st/stm32cubeide_1.10.0
    python3 tools/init_machine.py --redetect CUBEIDE forget a kept value and look again

This is the first thing to run on a new machine, before selfcheck. It searches
first and only asks about what it could not find, showing what the thing is,
where it already looked, and what a right answer looks like on this platform.

It asks ONLY when stdin is a terminal AND the environment does not look
automated (CLAUDECODE, AI_AGENT, CI, GITHUB_ACTIONS, INIT_MACHINE_NO_INPUT,
GIT_TERMINAL_PROMPT=0). Both tests are needed: an agent or a CI job can hold a
real console, so isatty() alone returns True there, and a prompt nobody is
watching hangs rather than failing. --ask overrides; --no-input forces off.
Whenever it does not ask, it says which of those reasons applied.

It replaces editing a template by hand, which had three failure modes worth
naming:

  - The template carries a Windows and a Linux value for every path and asks
    you to delete one. Forgetting is the common case, and it surfaces later as
    a pile of unrelated MISSING lines. (Debian, 2026-08-20.)
  - CORE_LIVE ends in the board-package version, so the template goes stale on
    every release. Here it is a glob, resolved at detection time.
  - machine.ps1 and machine.py were two hand-maintained files holding the same
    values, with nothing keeping them equal. Both are generated from the table
    below, so they cannot disagree.

Values already in config/machine.py are KEPT when they still make sense on this
platform and still exist on disk, so a deliberate choice survives a re-run. Use
--redetect to drop one, or --set to overwrite it.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
TESTTOOL_DIR = TOOLS_DIR.parent
CONFIG_DIR = TESTTOOL_DIR / "config"
TOOL_REPO_GUESS = TESTTOOL_DIR.parent

if sys.platform.startswith("win"):
    PLATFORM = "windows"
elif sys.platform == "darwin":
    PLATFORM = "macos"
else:
    PLATFORM = "linux"
IS_WIN = PLATFORM == "windows"
EXE = ".exe" if IS_WIN else ""
A15_DIR = {"windows": "win", "linux": "linux", "macos": "macosx"}[PLATFORM]
CUBE_PLUG = {"windows": "win32", "linux": "linux64", "macos": "macos64"}[PLATFORM]
HOME = Path.home()


def _c(text, code):
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            return text
    return "\033[%sm%s\033[0m" % (code, text)


def Section(t):
    print()
    print(_c("===== " + t, "36"))


def Ok(t):
    print(_c(t, "32"))


def Warn(t):
    print(_c(t, "33"))


def Fail(t):
    print(_c(t, "31"))


def newest(pattern):
    """Last match in sorted order. Board-package and CubeIDE plugin directories
    carry a version in the name, so the newest sorts last for every version
    scheme this project has used."""
    hits = sorted(glob.glob(str(pattern)))
    return hits[-1] if hits else None


def first_existing(candidates):
    for c in candidates:
        if not c:
            continue
        hit = newest(c) if "*" in str(c) else (str(c) if Path(c).exists() else None)
        if hit:
            return hit
    return None


# ---------------------------------------------------------------- detectors
def detect_repo(name):
    """Repos sit next to each other. This file is inside TOOL_REPO, so start
    from its own location rather than from a guess about where work lives."""
    def go():
        if name == "IAPTranfer_Tool":
            return str(TOOL_REPO_GUESS)
        parent = TOOL_REPO_GUESS.parent
        for base in (parent, parent.parent, HOME, HOME / "Documents"):
            for depth in ("%s", "*/%s"):
                hit = newest(str(Path(base) / (depth % name)))
                if hit and Path(hit, ".git").exists():
                    return hit
        return None
    return go


def detect_a15():
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        return first_existing([Path(local) / "Arduino15" if local else None])
    if PLATFORM == "macos":
        return first_existing([HOME / "Library" / "Arduino15"])
    return first_existing([HOME / ".arduino15"])


def detect_core_live():
    """The trailing directory is the board-package version and moves on every
    release, so never write it down -- resolve it."""
    a15 = RESOLVED.get("A15")
    if not a15:
        return None
    return newest(Path(a15) / "packages" / "OpenPLC_Alpha" / "hardware" / "stm32" / "*")


def cubeide_roots():
    """Kept out of the detector so the prompt can show where it looked. A person
    being asked to paste a path deserves to know which places were already
    tried."""
    if IS_WIN:
        return [r"C:\ST\STM32CubeIDE*", r"D:\ST\STM32CubeIDE*", r"E:\ST\STM32CubeIDE*",
                r"C:\Program Files\STMicroelectronics\STM32CubeIDE*",
                r"C:\Program Files (x86)\STMicroelectronics\STM32CubeIDE*",
                str(Path(os.environ.get("LOCALAPPDATA", "x")) / "Programs" / "STM32CubeIDE*")]
    if PLATFORM == "macos":
        return ["/Applications/STM32CubeIDE*", str(HOME / "Applications" / "STM32CubeIDE*")]
    return ["/opt/st/stm32cubeide*", "/opt/stm32cubeide*", "/usr/local/stm32cubeide*",
            str(HOME / "st" / "stm32cubeide*"), str(HOME / "stm32cubeide*")]


def detect_cubeide():
    for r in cubeide_roots():
        for hit in sorted(glob.glob(r)):
            # The install root is the directory holding STM32CubeIDE/plugins.
            # Matching on the name alone picks up shortcuts and unpacked
            # archives that no tool can be run from.
            if (Path(hit) / "STM32CubeIDE" / "plugins").is_dir():
                return hit
    return None


def ide_roots():
    if IS_WIN:
        return [str(Path(os.environ.get("LOCALAPPDATA", "x")) / "Programs" / "Arduino IDE"),
                r"C:\Program Files\Arduino IDE", r"D:\Soft\arduino*", r"C:\arduino*",
                r"D:\arduino*", r"C:\Program Files (x86)\Arduino IDE"]
    if PLATFORM == "macos":
        return ["/Applications/Arduino IDE.app/Contents"]
    return ["/opt/arduino-ide", "/usr/share/arduino-ide", "/usr/local/arduino-ide",
            str(HOME / ".local" / "share" / "arduino-ide"),
            str(HOME / "arduino-ide*"), "/opt/arduino*"]


def detect_ide():
    """Arduino IDE 2.x, identified by the arduino-cli it ships -- that binary is
    the only part of it any script here uses."""
    for r in ide_roots():
        for hit in sorted(glob.glob(r)):
            if (Path(hit) / "resources/app/lib/backend/resources" / ("arduino-cli" + EXE)).exists():
                return hit
    return None


def detect_arduino_cli():
    ide = RESOLVED.get("IDE")
    if ide:
        p = Path(ide) / "resources/app/lib/backend/resources" / ("arduino-cli" + EXE)
        if p.exists():
            return str(p)
    return None


def detect_arduino_cli_config():
    p = HOME / ".arduinoIDE" / "arduino-cli.yaml"
    return str(p) if p.exists() else None


def gcc_is_modern(exe):
    """Dev-C++ ships GCC 3.4.2 from 2004: it rejects -std=c11 outright and its
    linker crashes at -std=c99. Verifying code destined for arm-none-eabi-gcc
    12.x with that would be worse than not testing at all, so a compiler that
    old is not a find -- it is a trap. See CLAUDE.md."""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=20).stdout
    except Exception:
        return False, "could not be run"
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', out)
    if not m:
        return False, "printed no version"
    if int(m.group(1)) < 5:
        return False, "is GCC %s -- far too old, see CLAUDE.md" % m.group(0)
    return True, "GCC %s" % m.group(0)


def detect_host_cc():
    cands = []
    onpath = shutil.which("gcc") or shutil.which("clang")
    if onpath:
        cands.append(onpath)
    if IS_WIN:
        cands += [c for c in (
            newest(r"C:\mingw64\bin\gcc.exe"), newest(r"D:\Soft\mingw64\bin\gcc.exe"),
            newest(r"C:\msys64\mingw64\bin\gcc.exe"), newest(r"C:\TDM-GCC*\bin\gcc.exe"),
            newest(r"C:\Program Files\mingw*\bin\gcc.exe")) if c]
    for c in cands:
        good, why = gcc_is_modern(c)
        if good:
            NOTES.append("HOST_CC is %s (%s)" % (c, why))
            return c
        NOTES.append("rejected %s: it %s" % (c, why))
    return None


def detect_log_ports():
    """Ports that exist RIGHT NOW, most plausible first.

    This is a guess and cannot be anything else: the log port is whichever one
    the RS232 adapter enumerated as, and if the adapter is unplugged it is not
    here to be found. Unplugged is also the normal state of a desk between
    sessions -- on this Windows machine the registry held only two virtual ports
    while the real answer was COM5/COM4.

    So rank by how the device names itself. A USB serial bridge is a candidate;
    a motherboard 16550 or a virtual port from some other program is what is
    left when nothing is plugged in, and putting one of those first would send
    tools/ listening to silence.
    """
    usbish = re.compile(r'usbser|vcp|ftdibus|prolific|silabser|ch34|cp210|usb', re.I)
    decoy = re.compile(r'vserial|virtual|com0com|\\Device\\Serial\d', re.I)

    if IS_WIN:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DEVICEMAP\SERIALCOMM")
            found, i = [], 0
            while True:
                try:
                    device, port, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                found.append((device, port))
                i += 1
        except Exception:
            return None
        if not found:
            return None

        def rank(item):
            device, port = item
            if usbish.search(device):
                tier = 0
            elif decoy.search(device):
                tier = 2
            else:
                tier = 1
            return (tier, int(re.sub(r'\D', '', port) or 0))

        found.sort(key=rank)
        if all(decoy.search(d) for d, _ in found):
            NOTES.append("the only serial ports here look virtual or motherboard "
                         "(%s) -- plug the RS232 adapter in and re-run, or --set LOG_PORTS=COM5,COM4"
                         % ", ".join("%s=%s" % (d, p) for d, p in found))
        return [p for _, p in found]

    # /dev/ttyUSB* is FTDI and CH340; /dev/ttyACM* is CDC, including the ST-Link
    # VCP. Both are plausible for the RS232 log, so offer both, USB first.
    hits = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if not hits:
        NOTES.append("no /dev/ttyUSB* or /dev/ttyACM* right now -- plug the RS232 "
                     "adapter in and re-run, or --set LOG_PORTS=/dev/ttyUSB0")
    return hits or None


def detect_workspace():
    """The Eclipse workspace holding the bootloader project -- its parent, in
    every layout this project has used."""
    boot = RESOLVED.get("BOOT_REPO")
    return str(Path(boot).parent) if boot else None


# ---------------------------------------------------------------- the table
# kind: "path" must exist on disk; "ports" is a list; "plain" is written as-is.
SETTINGS = [
    ("__section__", "repositories",
     ["The three repos this product is built from.",
      "See open_plc_cube_ide/docs/ARCHITECTURE.md."]),
    ("BOOT_REPO", "path", detect_repo("open_plc_cube_ide"), True,
     ["bootloader, CubeIDE project, and the shared docs"]),
    ("CORE_REPO", "path", detect_repo("open_plc_arduino"), True,
     ["the Arduino board package, under version control"]),
    ("TOOL_REPO", "path", detect_repo("IAPTranfer_Tool"), True,
     ["this repo"]),

    ("__section__", "Arduino",
     ["A15 is Arduino's data directory. CORE_LIVE is the board package the IDE",
      "actually compiles against and is NOT under version control -- see",
      "ARCHITECTURE.md for the live -> repo direction.",
      "",
      "CORE_LIVE ends in the package version, which changes on release. It is",
      "detected rather than written down; re-run init_machine after an upgrade."]),
    ("A15", "path", detect_a15, True, []),
    ("CORE_LIVE", "path", detect_core_live, True, []),

    ("__section__", "toolchain",
     ["CubeIDE supplies both the headless builder and STM32_Programmer_CLI.",
      "This is the install root; the versioned plugin underneath it is resolved",
      "at run time, because hardcoding it breaks on every CubeIDE update."]),
    ("CUBEIDE", "path", detect_cubeide, False, []),
    ("WORKSPACE", "path", detect_workspace, False,
     ["Eclipse workspace holding the bootloader project"]),
    ("IAPTOOL", "plain", lambda: "", False,
     ["IAPTool ships inside the board package. Empty means: find it by wildcard",
      "under A15 for this platform, which is what you want -- the package",
      "version moves independently of the core. Set it only to force a build."]),

    ("__section__", "Arduino IDE 2.x",
     ["For rebuilding the application from the command line. The CLI it ships is",
      "NOT on PATH and is not the standalone arduino-cli release; the config",
      "file is what points it at Arduino15 packages and the user libraries."]),
    ("IDE", "path", detect_ide, False, []),
    ("ARDUINO_CLI", "path", detect_arduino_cli, False, []),
    ("ARDUINO_CLI_CONFIG", "path", detect_arduino_cli_config, False, []),

    ("__section__", "host C compiler",
     ["For host/bootloader_unit (case H2), which compiles the real bootloader C",
      "sources natively. Empty makes selfcheck report H2 as SKIP and name it,",
      "rather than pretending it passed.",
      "",
      "It must be a MODERN gcc/clang. init_machine rejects anything before GCC 5",
      "on sight: a Dev-C++ install ships GCC 3.4.2 (2004), which rejects -std=c11",
      "and whose linker crashes at -std=c99."]),
    ("HOST_CC", "path", detect_host_cc, False, []),

    ("__section__", "board",
     ["LOG_PORTS: where the bootloader/app printf lands. UART4 reaches RS232",
      "terminals C05/C06 -- real +/-12V levels, so this is an RS232 adapter, not",
      "a TTL one. Most likely port first; tools/ try them in order.",
      "",
      "These are the values init_machine can least justify guessing: it lists the",
      "ports that exist, not the one the board is on. Fix the order by hand or",
      "with --set once you know."]),
    ("LOG_PORTS", "ports", detect_log_ports, False, []),
    ("LOG_BAUD", "plain", lambda: 115200, False, []),
    ("CDC_PORT", "plain", lambda: "", False,
     ["the board's USB CDC, when enumerated"]),
    ("BOARD_IP", "plain", lambda: "192.168.0.7", False,
     ["not a property of this machine, but of the board on your desk"]),
]

RESOLVED = {}
SOURCE = {}
NOTES = []

# ---------------------------------------------------------------- asking
# What to tell someone who has to paste a path: what the thing is, where this
# script already looked, and what a right answer looks like here. Without the
# second line the reply to "not found" is reasonably "but it IS installed".
EXAMPLES = {
    "windows": {
        "BOOT_REPO": r"E:\WorkSpace\Schaeffer-AG\open_plc_cube_ide",
        "CORE_REPO": r"E:\WorkSpace\Schaeffer-AG\open_plc_arduino",
        "TOOL_REPO": r"E:\WorkSpace\Schaeffer-AG\IAPTranfer_Tool",
        "A15": r"C:\Users\you\AppData\Local\Arduino15",
        "CORE_LIVE": r"C:\Users\you\AppData\Local\Arduino15\packages\OpenPLC_Alpha\hardware\stm32\0.1.3-pre",
        "CUBEIDE": r"D:\ST\STM32CubeIDE_1.10.0",
        "IDE": r"D:\Soft\arduino-2",
        "ARDUINO_CLI": r"D:\Soft\arduino-2\resources\app\lib\backend\resources\arduino-cli.exe",
        "ARDUINO_CLI_CONFIG": r"C:\Users\you\.arduinoIDE\arduino-cli.yaml",
        "HOST_CC": r"D:\Soft\mingw64\bin\gcc.exe",
        "WORKSPACE": r"E:\WorkSpace\Schaeffer-AG",
        "LOG_PORTS": "COM5,COM4",
        "CDC_PORT": "COM6",
    },
    "posix": {
        "BOOT_REPO": "/home/you/Documents/WorkSpace/open_plc_cube_ide",
        "CORE_REPO": "/home/you/Documents/WorkSpace/open_plc_arduino",
        "TOOL_REPO": "/home/you/Documents/WorkSpace/IAPTranfer_Tool",
        "A15": "/home/you/.arduino15",
        "CORE_LIVE": "/home/you/.arduino15/packages/OpenPLC_Alpha/hardware/stm32/0.1.3-pre",
        "CUBEIDE": "/opt/st/stm32cubeide_1.10.0",
        "IDE": "/opt/arduino-ide",
        "ARDUINO_CLI": "/opt/arduino-ide/resources/app/lib/backend/resources/arduino-cli",
        "ARDUINO_CLI_CONFIG": "/home/you/.arduinoIDE/arduino-cli.yaml",
        "HOST_CC": "/usr/bin/gcc",
        "WORKSPACE": "/home/you/Documents/WorkSpace",
        "LOG_PORTS": "/dev/ttyUSB0,/dev/ttyACM0",
        "CDC_PORT": "/dev/ttyACM1",
    },
}

WHAT_IT_IS = {
    "BOOT_REPO": "the open_plc_cube_ide clone -- bootloader plus the shared docs",
    "CORE_REPO": "the open_plc_arduino clone -- the board package under git",
    "TOOL_REPO": "this repo, IAPTranfer_Tool",
    "A15": "Arduino's data directory, the one holding packages/",
    "CORE_LIVE": "the installed OpenPLC_Alpha board package the IDE compiles against",
    "CUBEIDE": "the STM32CubeIDE install ROOT -- the directory that contains STM32CubeIDE/plugins",
    "WORKSPACE": "the Eclipse workspace holding the bootloader project",
    "IDE": "the Arduino IDE 2.x install ROOT -- the directory containing resources/app",
    "ARDUINO_CLI": "the arduino-cli that ships inside the IDE (not a standalone release)",
    "ARDUINO_CLI_CONFIG": "arduino-cli.yaml, which points the CLI at Arduino15 and user libraries",
    "HOST_CC": "a modern gcc or clang for the host-side C tests (GCC 5 or newer)",
    "LOG_PORTS": "serial port(s) carrying the bootloader/app printf, most likely first",
    "CDC_PORT": "the board's USB CDC port, when it is enumerated",
    "BOARD_IP": "the board's IP address",
    "IAPTOOL": "a specific IAPTool build; normally left empty so it is found by wildcard",
}


def searched_in(key):
    if key == "CUBEIDE":
        return cubeide_roots()
    if key == "IDE":
        return ide_roots()
    if key == "CORE_LIVE":
        a15 = RESOLVED.get("A15") or "<A15>"
        return [str(Path(a15) / "packages/OpenPLC_Alpha/hardware/stm32/*")]
    if key in ("BOOT_REPO", "CORE_REPO", "TOOL_REPO"):
        return ["next to %s, and one level further out" % TOOL_REPO_GUESS.parent]
    if key == "HOST_CC":
        return ["gcc or clang on PATH"] + (
            [r"C:\mingw64\bin", r"C:\msys64\mingw64\bin", r"D:\Soft\mingw64\bin"] if IS_WIN else [])
    if key == "LOG_PORTS":
        return [r"HKLM\HARDWARE\DEVICEMAP\SERIALCOMM"] if IS_WIN else \
               ["/dev/ttyUSB*", "/dev/ttyACM*"]
    return []


def validate(key, value):
    """Catch the paste that is one directory off, which is the likely mistake
    for the two settings whose right answer is an install root."""
    p = Path(value)
    if key == "CUBEIDE":
        if not (p / "STM32CubeIDE" / "plugins").is_dir():
            return False, "no STM32CubeIDE/plugins under it -- this is probably one level too high or too low"
    if key == "IDE":
        if not (p / "resources/app/lib/backend/resources" / ("arduino-cli" + EXE)).exists():
            return False, "no bundled arduino-cli under it -- expected resources/app/lib/backend/resources/"
    if key == "A15":
        if not (p / "packages").is_dir():
            return False, "no packages/ under it -- that directory is what makes this Arduino's data dir"
    return True, ""


def describe(key, kind, indent="    "):
    """Everything a person needs in order to supply this value.

    Used both by the prompt and by the report printed when this runs without a
    terminal -- which is the normal case, because the usual caller is an agent
    that will relay the question to the user and come back with --set. The two
    must say the same thing, so they share this.
    """
    if key in WHAT_IT_IS:
        print("%swhat it is : %s" % (indent, WHAT_IT_IS[key]))
    where = searched_in(key)
    if where:
        print("%slooked in  : %s" % (indent, where[0]))
        for w in where[1:]:
            print("%s             %s" % (indent, w))
    ex = EXAMPLES["windows" if IS_WIN else "posix"].get(key)
    if ex:
        print("%sexample    : %s" % (indent, ex))
    if kind == "ports":
        print("%sformat     : comma-separated, most likely first" % indent)


def ask_for(key, kind, required):
    """Ask a human directly. Returns a value, or None if they chose to skip.

    Never called unless stdin is a terminal AND nothing suggests automation:
    prompting a console nobody is watching hangs, and this script is run from
    automation more often than by hand.
    """
    print()
    Warn("  %s was not found." % key)
    describe(key, kind)

    hint = "path" if kind != "ports" else "port(s)"
    for _ in range(5):
        try:
            raw = input("    paste the %s (Enter to skip): " % hint).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            if required:
                Warn("    nothing works without %s. Enter again to give up." % key)
                try:
                    if not input("    paste the %s: " % hint).strip():
                        return None
                except (EOFError, KeyboardInterrupt):
                    return None
                continue
            return None

        # Explorer and most file managers copy paths wrapped in quotes, and a
        # tilde is not expanded by anything when it arrives as text.
        raw = raw.strip().strip('"').strip("'")
        raw = os.path.expanduser(os.path.expandvars(raw))

        if kind == "ports":
            return [p.strip() for p in raw.split(",") if p.strip()]
        if kind == "plain":
            return raw
        if not Path(raw).exists():
            Fail("    that path does not exist: %s" % raw)
            continue
        ok, why = validate(key, raw)
        if not ok:
            Fail("    %s" % why)
            continue
        return raw
    Warn("    giving up on %s" % key)
    return None


# ---------------------------------------------------------------- existing
def load_existing():
    """Read the current machine.py, if any, so deliberate values survive."""
    path = CONFIG_DIR / "machine.py"
    if not path.exists():
        return {}
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("machine_old", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        Warn("  the existing config/machine.py could not be imported (%s);" % e)
        Warn("  treating every value as unset")
        return {}
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}


def looks_wrong_platform(value):
    if not isinstance(value, str) or not value:
        return False
    if IS_WIN:
        return value.startswith("/")
    return bool(re.match(r'^[A-Za-z]:[\\/]', value)) or "\\" in value


def keepable(key, kind, value):
    """A previous value is kept only if it could still be true here."""
    if value is None or value == "":
        return False
    if kind == "ports":
        if not isinstance(value, (list, tuple)) or not value:
            return False
        return not any(looks_wrong_platform(p) or (not IS_WIN and re.match(r'^COM\d+$', str(p), re.I))
                       for p in value)
    if kind == "plain":
        return not looks_wrong_platform(value)
    if looks_wrong_platform(value):
        return False
    return Path(str(value)).exists()


# ---------------------------------------------------------------- rendering
def py_literal(value):
    if isinstance(value, bool) or isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join('"%s"' % str(v).replace('"', '\\"') for v in value) + "]"
    s = str(value)
    if "\\" in s:
        return 'r"%s"' % s
    return '"%s"' % s.replace('"', '\\"')


def ps_literal(value):
    """Single-quoted, so a $ or a backtick in a path cannot be interpolated."""
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "@(" + ", ".join("'%s'" % str(v).replace("'", "''") for v in value) + ")"
    return "'%s'" % str(value).replace("'", "''")


def header_lines():
    runner = "python" if IS_WIN else "python3"
    return [
        "Generated by tools/init_machine.py on %s. Do not put machine-specific" % PLATFORM,
        "paths anywhere else -- this file is the only place they are allowed.",
        "",
        "Safe to edit by hand: re-running init_machine keeps any value that still",
        "exists and still suits this platform. To make it look again for one,",
        "  %s tools/init_machine.py --redetect CUBEIDE" % runner,
        "or to set one outright,",
        "  %s tools/init_machine.py --set CUBEIDE=/opt/st/stm32cubeide_1.10.0" % runner,
        "",
        "This file is gitignored. The committed record of what belongs here is the",
        "SETTINGS table in tools/init_machine.py, not a template to copy by hand.",
    ]


def render(comment_prefix, assign, literal, values):
    out = []
    for text in header_lines():
        out.append((comment_prefix + " " + text).rstrip())
    for entry in SETTINGS:
        if entry[0] == "__section__":
            _, title, comments = entry
            out.append("")
            out.append(comment_prefix + " --- " + title + " " +
                       "-" * max(3, 74 - len(title)))
            for c in comments:
                out.append((comment_prefix + " " + c).rstrip())
            continue
        key, kind, _detect, _req, comments = entry
        for c in comments:
            out.append((comment_prefix + " " + c).rstrip())
        v = values.get(key)
        if v is None:
            v = [] if kind == "ports" else ""
        out.append("%s = %s" % (assign(key), literal(v)))
    return "\n".join(out) + "\n"


def render_python(values):
    body = render("#", lambda k: k, py_literal, values)
    return '"""Machine-local paths. Imported by tools/common.py."""\n' + body


def render_powershell(values):
    head = ("# Machine-local paths. Dot-sourced by tools/_common.ps1.\n"
            "#\n"
            "# Kept in step with machine.py by tools/init_machine.py, which writes both.\n"
            "# It stops being written once M7 removes the PowerShell scripts.\n")
    return head + render("#", lambda k: "$" + k, ps_literal, values)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="detect and report, write nothing")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="force a value; repeatable")
    ap.add_argument("--redetect", action="append", default=[], metavar="NAME",
                    help="ignore the current value for NAME and detect it again")
    ap.add_argument("--no-powershell", action="store_true",
                    help="write only machine.py")
    ap.add_argument("--no-input", action="store_true",
                    help="never ask; just report what could not be found")
    ap.add_argument("--ask", action="store_true",
                    help="ask even when the environment looks automated")
    args = ap.parse_args()

    # Asking is the default, but only when somebody is there to answer. A prompt
    # written to a pipe raises EOFError and is handled; a prompt written to a
    # console that nobody is watching just hangs, which is worse, and that is
    # exactly the shape of an agent or CI job holding a real console. isatty()
    # returns True in those, so it cannot be the only test.
    markers = [v for v in ("CLAUDECODE", "AI_AGENT", "CI", "GITHUB_ACTIONS",
                           "INIT_MACHINE_NO_INPUT") if os.environ.get(v)]
    if os.environ.get("GIT_TERMINAL_PROMPT") == "0":
        markers.append("GIT_TERMINAL_PROMPT=0")
    automated = bool(markers) and not args.ask

    interactive = sys.stdin.isatty() and not automated and not args.no_input and not args.check
    why_not = None
    if not interactive:
        if args.check:
            why_not = "--check never asks"
        elif args.no_input:
            why_not = "--no-input was passed"
        elif automated:
            why_not = "this looks automated (%s) -- pass --ask to override" % ", ".join(markers)
        elif not sys.stdin.isatty():
            why_not = "stdin is not a terminal"

    forced = {}
    for item in args.set:
        if "=" not in item:
            Fail("--set wants NAME=VALUE, got: %s" % item)
            return 2
        k, v = item.split("=", 1)
        forced[k.strip().upper()] = v

    keys = [e[0] for e in SETTINGS if e[0] != "__section__"]
    for k in list(forced) + [r.upper() for r in args.redetect]:
        if k not in keys:
            Fail("no such setting: %s" % k)
            Warn("  known: %s" % ", ".join(keys))
            return 2

    Section("init_machine")
    print("  %-19s %s   (Python %d.%d.%d)" % (
        "platform", PLATFORM, *sys.version_info[:3]))
    print("  %-19s %s" % ("config directory", CONFIG_DIR))

    existing = load_existing()
    redetect = {r.upper() for r in args.redetect}

    def is_empty(v):
        return v is None or v == "" or v == []

    entries = [e for e in SETTINGS if e[0] != "__section__"]

    # Pass 1: forced value, or a previous value still worth keeping, or detection.
    # In SETTINGS order, so a detector may use what an earlier one resolved --
    # CORE_LIVE needs A15, ARDUINO_CLI needs IDE.
    for key, kind, detect, required, _ in entries:
        if key in forced:
            value, source = forced[key], "given"
            if kind == "ports":
                value = [p.strip() for p in value.split(",") if p.strip()]
            elif kind == "plain" and str(value).isdigit():
                value = int(value)
            # A --set value is taken as given -- the caller may be pointing at
            # something not installed yet -- but a typo must not pass in
            # silence. That is an hour of debugging the wrong thing.
            elif kind == "path" and not Path(str(value)).exists():
                NOTES.append("%s was given as %s, which does not exist" % (key, value))
        elif key not in redetect and keepable(key, kind, existing.get(key)):
            value, source = existing[key], "kept"
        else:
            value, source = detect(), "detected"
            # Falling back to the previous value protects a hand-set path from a
            # detector that regressed -- but NOT when --redetect asked for this
            # one specifically. Restoring it there would ignore the request and
            # report success for something that did not happen.
            if (value is None and key not in redetect
                    and key in existing and keepable(key, kind, existing.get(key))):
                value, source = existing[key], "kept"
        RESOLVED[key] = value
        SOURCE[key] = source

    # Pass 2: ask for what is still missing. Detection runs once more first --
    # an answer given a moment ago can be all a later detector was waiting for,
    # so supplying A15 by hand should not also mean supplying CORE_LIVE.
    if interactive:
        pending = [(k, ki, d, r) for k, ki, d, r, _ in entries
                   if is_empty(RESOLVED[k]) and k not in ("IAPTOOL", "CDC_PORT")]
        if pending:
            Section("not found automatically -- paste them in")
            print("  Enter alone skips one. Skipping only limits what can run;")
            print("  selfcheck names every check it had to skip and why.")
        for key, kind, detect, required in pending:
            again = detect()
            if not is_empty(again):
                RESOLVED[key] = again
                SOURCE[key] = "detected"
                Ok("  %-19s %-9s %s" % (key, "detected", again))
                continue
            answer = ask_for(key, kind, required)
            if not is_empty(answer):
                RESOLVED[key] = answer
                SOURCE[key] = "pasted"

    Section("settings")
    missing_required, missing_optional = [], []
    for key, kind, _detect, required, _ in entries:
        value, source = RESOLVED[key], SOURCE[key]
        if is_empty(value):
            # IAPTOOL and CDC_PORT are meant to be empty; anything else that
            # came up empty is a thing this machine does not have.
            if required:
                Warn("  %-19s %-9s MISSING" % (key, ""))
                missing_required.append(key)
            else:
                print("  %-19s %-9s %s" % (key, "", "(empty)"))
                if key not in ("IAPTOOL", "CDC_PORT"):
                    missing_optional.append(key)
            continue
        shown = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        line = "  %-19s %-9s %s" % (key, source, shown)
        if source in ("detected", "pasted"):
            Ok(line)
        else:
            print(line)

    for n in NOTES:
        print("  note: %s" % n)

    if not IS_WIN:
        check_dialout()

    kinds = {k: ki for k, ki, _d, _r, _c in entries}
    runner = "python" if IS_WIN else "python3"

    def report_missing(keys, title, why_it_matters):
        """The whole point of the non-interactive path.

        Its reader is usually an agent that will relay these questions to the
        person at the keyboard and come back with --set, so a bare list of names
        is not enough: it has to carry what each thing is, where the search
        already went, and the exact command that records the answer.
        """
        Section(title)
        print("  %s" % why_it_matters)
        for k in keys:
            print()
            Warn("  %s" % k)
            describe(k, kinds[k], indent="      ")
            print("      record it  : %s tools/init_machine.py --set %s=<path>"
                  % (runner, k))
        print()
        print("  Several at once:")
        print("    %s tools/init_machine.py %s" % (
            runner, " ".join("--set %s=<path>" % k for k in keys)))

    if missing_required:
        report_missing(missing_required, "cannot write a usable config yet",
                       "Nothing works without these.")
        print()
        if interactive:
            Warn("  Skipped at the prompt. Re-run to be asked again.")
        else:
            Warn("  Not asked for interactively, because %s." % why_not)
        return 1

    if missing_optional:
        report_missing(missing_optional, "not found -- these limit what can be run",
                       "Everything else is written; these are the checks that will "
                       "report SKIP until they are supplied.")
        print()
        if interactive:
            Warn("  Skipped at the prompt. Install one and re-run, or --set it.")
        else:
            Warn("  Not asked for interactively, because %s." % why_not)
        Warn("  open_plc_cube_ide/CLAUDE.md says what each one is and where to get it.")

    Section("files")
    targets = [("machine.py", render_python(RESOLVED))]
    if not args.no_powershell:
        targets.append(("machine.ps1", render_powershell(RESOLVED)))

    for name, text in targets:
        path = CONFIG_DIR / name
        if args.check:
            state = "would change" if (not path.exists() or
                                       path.read_text(encoding="utf-8") != text) else "unchanged"
            print("  %-14s %s" % (name, state))
            continue
        # Keep the first version this ever replaced. A config that took an
        # afternoon to get right must not be lost to one confident detector,
        # and backing up only once means a later run cannot bury the original
        # under a generated copy of itself.
        backup = path.with_suffix(path.suffix + ".bak")
        if path.exists() and not backup.exists():
            shutil.copy2(str(path), str(backup))
            Warn("  kept the previous %s as %s" % (name, backup.name))
        path.write_text(text, encoding="utf-8", newline="\n")
        Ok("  wrote %s" % path)

    if args.check:
        Warn("  --check: nothing was written")
        return 0

    Section("next")
    print("  python%s tools/common.py --probe        confirm what A0 now sees"
          % ("" if IS_WIN else "3"))
    print("  pwsh ./tools/selfcheck.ps1              full host-side run (needs PowerShell)")
    return 0


def check_dialout():
    """Serial access on Linux needs group membership, and the failure without it
    reads like a missing adapter rather than a missing group."""
    try:
        import grp
        groups = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except Exception:
        return
    if "dialout" in groups or "uucp" in groups:
        return
    Warn("  this user is in neither the dialout nor the uucp group.")
    Warn("    Every serial open will fail with a permission error that looks")
    Warn("    like a missing adapter.  sudo usermod -aG dialout $USER")
    Warn("    then log out and back in -- a new shell is not enough.")


if __name__ == "__main__":
    sys.exit(main())
