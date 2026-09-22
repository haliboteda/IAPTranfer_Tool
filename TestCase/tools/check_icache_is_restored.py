"""Turning the I-cache off must be undone on every path out of the function.

Case P16, evidence for R1-25 (a failed upload must not damage the installed
app). $PROD/docs/engineering/HOW-TO-RUN-TESTS.md holds the criterion.

Flash_If_Write() disables the I-cache and unlocks the flash, then writes in a
loop. Returning straight out of that loop on the first bad word skips both
HAL_FLASH_Lock() and SCB_EnableICache(): the flash stays UNLOCKED for the rest
of the run, so any later stray write can reach the installed app. That is the
requirement this defends, and it is a property of every exit path -- forcing one
failing write only exercises one of them, which is why this is a source check
and not a board case.

The rule, per SCB_DisableICache() site:

  * a matching SCB_EnableICache() must follow in the same function;
  * no `return` may sit between the two;
  * HAL_FLASH_Lock() must come before the re-enable, when the function locks at
    all (the erase and write paths both do).

EXEMPT names a site that disables the cache and deliberately never re-enables.
It is a list, not a silent skip: a new one has to be argued for here.

Exit code is the verdict: 0 every site restores what it turned off, 1 one does not.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, cfg  # noqa: E402

BOOT = Path(cfg.BOOT_REPO)

# Files that may touch the I-cache at all. Anything outside this list is a
# finding in itself -- ST's own Drivers/ are not scanned, we do not own them.
SCANNED = [
    Path("Core/Src/usbd_cdc_flash.c"),
    Path("IAPServer/IAP_server.c"),
    Path("IAPServer/boot_selfupgrade.c"),
]

# site -> why it never re-enables. The jump leaves this image for good, so there
# is no "after" in which a re-enable could run; the application's own SystemInit
# decides its cache state.
EXEMPT = {
    "server_jump_to_app": "jumps into the app and never returns",
    # Same shape, one step further: the sector holding this code is about to
    # be erased, so there is no "after" here either -- it resets the board.
    "arm_and_burn": "rewrites sector 0 from RAM and resets; the only path past the disable ends in NVIC_SystemReset",
}

DISABLE = "SCB_DisableICache()"
ENABLE = "SCB_EnableICache()"
LOCK = "HAL_FLASH_Lock()"

# A function header at column 0: `type name(args)`, brace trailing or on the
# next line. __attribute__((...)) is stripped first -- left in, its inner parens
# make the name come out as "_attribute__".
ATTR = re.compile(r"__attribute__\s*\(\(.*?\)\)")
FUNC = re.compile(r"^[A-Za-z_][A-Za-z0-9_ \*]*?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*?\)\s*\{?\s*$")
NOT_A_HEADER = ("if", "for", "while", "switch", "return", "else", "do")


def enclosing_function(lines, idx):
    """Name of the function the line at idx sits in, or None."""
    for i in range(idx, -1, -1):
        raw = lines[i].rstrip()
        if raw.lstrip().startswith(NOT_A_HEADER):
            continue
        m = FUNC.match(ATTR.sub("", raw).strip())
        if m:
            return m.group(1)
    return None


def function_end(lines, idx):
    """Index of the line holding the closing brace of the function around idx."""
    for i in range(idx, len(lines)):
        if lines[i].rstrip() == "}":
            return i
    return len(lines) - 1


def check_file(rel):
    path = BOOT / rel
    if not path.exists():
        Fail("  missing: %s" % rel.as_posix())
        return 1
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return scan_lines(rel.as_posix(), lines)


def scan_lines(label, lines, quiet=False):
    def ok(msg):
        if not quiet:
            Ok(msg)

    def bad(msg):
        if not quiet:
            Fail(msg)

    problems = 0
    found = 0
    for idx, line in enumerate(lines):
        if DISABLE not in line:
            continue
        found += 1
        fname = enclosing_function(lines, idx) or "<unknown>"
        where = "%s:%d (%s)" % (label, idx + 1, fname)
        if fname in EXEMPT:
            ok("  %-58s exempt -- %s" % (where, EXEMPT[fname]))
            continue
        end = function_end(lines, idx)
        body = lines[idx + 1:end + 1]
        enable_at = next((k for k, l in enumerate(body) if ENABLE in l), None)
        if enable_at is None:
            bad("  %-58s turns the I-cache off and never turns it back on" % where)
            problems += 1
            continue
        between = body[:enable_at]
        returns = [k for k, l in enumerate(between) if re.search(r"\breturn\b", l)]
        if returns:
            bad("  %-58s has %d return(s) before %s" % (where, len(returns), ENABLE))
            bad("     line %d leaves the flash unlocked -- use one exit"
                % (idx + 2 + returns[0]))
            problems += 1
            continue
        lock_at = next((k for k, l in enumerate(body) if LOCK in l), None)
        if lock_at is not None and lock_at > enable_at:
            bad("  %-58s locks the flash AFTER re-enabling the cache" % where)
            problems += 1
            continue
        ok("  %-58s single exit, lock then re-enable" % where)
    if not found:
        bad("  %s: no %s at all -- did the file move?" % (label, DISABLE))
        problems += 1
    return problems


# Sources this check MUST reject. Without them a broken rule -- a regex that
# stopped matching, a scan that reads no lines -- would pass forever, and a
# check that cannot fail is not evidence of anything.
MUST_FAIL = {
    "early return inside the loop": """
uint16_t Flash_If_Write(uint8_t *d, uint8_t *f, uint32_t len)
{
  SCB_DisableICache();
  HAL_FLASH_Unlock();
  if (HAL_FLASH_Program(0, 0, 0) != HAL_OK)
  {
    return HAL_ERROR;
  }
  HAL_FLASH_Lock();
  SCB_EnableICache();
  return HAL_OK;
}
""",
    "never re-enabled": """
uint16_t Flash_If_Write(uint8_t *d, uint8_t *f, uint32_t len)
{
  SCB_DisableICache();
  HAL_FLASH_Unlock();
  HAL_FLASH_Lock();
  return HAL_OK;
}
""",
    "locked after the cache came back": """
uint16_t Flash_If_Write(uint8_t *d, uint8_t *f, uint32_t len)
{
  SCB_DisableICache();
  HAL_FLASH_Unlock();
  SCB_EnableICache();
  HAL_FLASH_Lock();
  return HAL_OK;
}
""",
}

MUST_PASS = {
    "one exit, lock then re-enable": """
uint16_t Flash_If_Write(uint8_t *d, uint8_t *f, uint32_t len)
{
  uint16_t result = HAL_OK;
  SCB_DisableICache();
  HAL_FLASH_Unlock();
  if (HAL_FLASH_Program(0, 0, 0) != HAL_OK)
  {
    result = HAL_ERROR;
  }
  HAL_FLASH_Lock();
  SCB_EnableICache();
  return result;
}
""",
}


def selftest():
    """The rule has to reject what it is there to reject."""
    Section("P16 selftest -- the rule still catches a bad exit")
    bad = 0
    for name, src in MUST_FAIL.items():
        if scan_lines(name, src.splitlines(), quiet=True) > 0:
            Ok("  rejected: %s" % name)
        else:
            Fail("  ACCEPTED a source it must reject: %s" % name)
            bad += 1
    for name, src in MUST_PASS.items():
        if scan_lines(name, src.splitlines(), quiet=True) == 0:
            Ok("  accepted: %s" % name)
        else:
            Fail("  REJECTED a source it must accept: %s" % name)
            bad += 1
    return bad


def main():
    problems = selftest()
    print("")
    Section("I-cache restored on every exit")
    problems += sum(check_file(rel) for rel in SCANNED)
    print("")
    if problems:
        Fail("%d site(s) can leave the I-cache off or the flash unlocked" % problems)
        return 1
    Ok("every site that disables the I-cache restores it, or is exempt with a reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
