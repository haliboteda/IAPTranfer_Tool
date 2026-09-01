"""P6 -- the "this board trusts the published root" warning still knows which
key the published root is.

    python tools/check_public_root.py            check
    python tools/check_public_root.py --print    also print a C initialiser

The bootloader warns on every boot when the root it verifies against is the one
shipped with the project, whose private half is in the repository. It recognises
that key by a SHA-256 fingerprint compiled into IAPServer/owner_slot.c.

That fingerprint is a CONSTANT on purpose. Deriving it from fw_pubkey.inc at
build time would make the comparison true for every build, so the warning would
also fire on a customer board built with the customer's own key -- and a warning
everyone learns to ignore protects nobody. See docs/design/OWNERSHIP.md.

The cost of it being a constant is that rotating the project's default key
(IAPServer/keys/rotate_keys.sh) leaves it pointing at the OLD key. Factory
boards would then trust a published key and say nothing, which is the exact
failure this warning exists to prevent. Nothing else would notice: the firmware
builds, boots, and looks healthy.

So this compares the two and fails when they drift.

In a customer's fork the two are SUPPOSED to differ -- that is what having their
own root means. This check belongs to this repository, where the default key is
by definition the published one.

Exit 0 = they agree, 1 = drifted, 2 = could not read one of the files.

"""

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cfg, Section, Ok, Warn, Fail, read_text          # noqa: E402

want_print = "--print" in sys.argv

inc = Path(cfg.BOOT_REPO) / "IAPServer" / "keys" / "fw_pubkey.inc"
src = Path(cfg.BOOT_REPO) / "IAPServer" / "owner_slot.c"
for f in (inc, src):
    if not f.exists():
        Fail("not found: %s" % f)
        sys.exit(2)

Section("published root fingerprint")

# The key as the firmware sees it: 64 raw bytes, X||Y.
hex_str = "".join(re.findall(r'0x([0-9a-fA-F]{2})', read_text(inc)))
if len(hex_str) != 128:
    Fail("fw_pubkey.inc parsed to %d hex chars, expected 128" % len(hex_str))
    sys.exit(2)
key_bytes = bytes.fromhex(hex_str)
want = hashlib.sha256(key_bytes).hexdigest()

# The constant the firmware compares against.
text = read_text(src)
m = re.search(r'k_published_root_sha256\s*\[\s*32\s*\]\s*=\s*\{(?P<body>[^}]*)\}', text)
if not m:
    Fail("could not find k_published_root_sha256[32] in owner_slot.c")
    sys.exit(2)
have = "".join(re.findall(r'0x([0-9a-fA-F]{2})', m.group("body")))

print("  key      %s..." % hex_str[:32])
print("  computed %s" % want)
print("  compiled %s" % have)

if want_print:
    Section("C initialiser")
    line = ""
    for i in range(32):
        line += "0x%s, " % want[i * 2:i * 2 + 2]
        if i % 8 == 7:
            print("\t" + line.rstrip())
            line = ""

# --- and does the built image actually contain that key? ---------------------
#
# The check above only compares two source files. It cannot see a build that
# still holds an older key, and that is a real way to be wrong: swapping
# fw_pubkey.inc for a file with an OLDER timestamp (any plain copy or restore
# preserves the source's timestamp) leaves make thinking fw_pubkey.o is current.
# The firmware then builds and boots perfectly while trusting the previous root.
#
# It happened during development, and the only reason it was noticed is that the
# board stopped verifying an application signed with the intended key. On a
# rotation where both keys are ours, nothing would have looked wrong at all.
stale_build = False
binary = Path(cfg.BOOT_REPO) / "Debug" / "open_plc_cube_ide.bin"
if binary.exists():
    Section("built image")
    found = binary.read_bytes().find(key_bytes)
    if found >= 0:
        Ok("  the key from fw_pubkey.inc is in Debug/*.bin at 0x%X" % found)
    else:
        Warn("  Debug/*.bin does NOT contain the key from fw_pubkey.inc")
        Warn("  -- the build is stale. Touch fw_pubkey.inc and rebuild.")
        stale_build = True
else:
    print("  (no Debug/*.bin to check; build the bootloader to include this)")

Section("result")
if len(have) != 64:
    Fail("the compiled constant parsed to %d hex chars, expected 64" % len(have))
    sys.exit(2)
# Compared case-insensitively. These are hex digests, so an
# owner_slot.c written with upper-case bytes would pass there and fail here --
# a verdict change, not a cosmetic one.
if have.lower() != want.lower():
    Fail("DRIFT: owner_slot.c does not recognise the key currently in fw_pubkey.inc.")
    Fail("Factory boards would trust a published root and stay silent about it.")
    Fail("Regenerate the constant (--print gives it) and rebuild the bootloader.")
    sys.exit(1)
if stale_build:
    Fail("the fingerprint is right, but the built image carries a different key")
    sys.exit(1)
Ok("the warning recognises the published root, and the build carries it")
sys.exit(0)
