"""Put a hand-made owner record into the board's owner slot area, for testing the
bootloader's record handling (requirement R2-02, module M1).

    python3 tools/inject_owner_record.py                  one record, generation 1
    python3 tools/inject_owner_record.py --generation 7   pick the generation
    python3 tools/inject_owner_record.py --cleared        a factory-reset record
    python3 tools/inject_owner_record.py --corrupt        wrong format_ver, must be ignored
    python3 tools/inject_owner_record.py --v1             the previous format, must be ignored
    python3 tools/inject_owner_record.py --wrong-uid      another board's uid, must be ignored
    python3 tools/inject_owner_record.py --restore        put the plain bootloader back

⚠️ WHY THIS IS NOT JUST "PROGRAMMER, WRITE 160 BYTES AT 0x0801E000"

The owner area lives in the top 8K of the bootloader's OWN flash sector.
STM32_Programmer_CLI erases a sector before writing into it, so a plain

    STM32_Programmer_CLI -c port=SWD -w record.bin 0x0801E000

erases the bootloader and leaves a board that prints nothing at all. That was
established the hard way; the board needed a reflash to come back.

So the record has to be flashed TOGETHER with the bootloader: this script pads
the bootloader image out to the start of the owner area, appends the record, and
writes the result as one image at 0x08000000. One erase, and both parts survive it.

Once the bootloader can append records itself (M1 step 4) this stays useful for
the cases that firmware is not supposed to be able to produce -- a record from a
future format version, or one with a signature that does not verify.

Exit 0 = flashed, 1 = the board did not report an owner slot, 2 = prerequisites missing.
"""

import argparse
import re
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Fail,  # noqa: E402
                    assert_target_reachable, get_programmer_cli,
                    open_log_ports, read_log_ports, run_capture, tcp_command)

# Must match owner_slot.h and the FLASH LENGTH in STM32H743IIKX_FLASH.ld.
OWNER_BASE = 0x0801E000
OWNER_OFFSET = OWNER_BASE - 0x08000000      # 0x1E000 = 122880
RECORD_SIZE = 160
OWNER_FORMAT_VER = 2
UID_LEN = 12

INTERESTING = re.compile(r"Owner slot|Bootloader state|APP Mod|UPLOAD Mod|"
                         r"NOT in effect|Reset cause|PUBLISHED|Claim it")


def record(generation, format_ver, flags, key_hex, filler, uid=b""):
    """One 160-byte owner record. prev_sig and reserved stay zero.

    v2 put uid between root_pubkey and prev_sig, so the offsets below are the
    v2 ones; a v1 record is the same 160 bytes with those 12 held at zero,
    which is what --v1 produces.
    """
    rec = bytearray(RECORD_SIZE)
    rec[0] = 0x4F                                       # type 'O'
    rec[1] = 5                                          # slots
    rec[2:4] = struct.pack("<H", format_ver)
    rec[4:8] = struct.pack("<I", generation)
    rec[8:12] = struct.pack("<I", flags)
    if key_hex:
        rec[12:76] = bytes.fromhex(key_hex)
    elif filler is not None:
        rec[12:76] = bytes([filler]) * 64
    if uid:
        rec[76:76 + UID_LEN] = uid
    return bytes(rec)


def board_uid(ip, port):
    """The board's own UID, which a v2 record has to carry to be accepted.

    Asked of the board rather than passed in: the whole point of the field is
    that it names one specific board, so a value typed by hand is a value that
    can be wrong without anything noticing.
    """
    text = tcp_command(ip, port, "getuid").strip().lower()
    if len(text) != UID_LEN * 2:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--key", default="")       # 128 hex chars; default is a recognisable pattern
    ap.add_argument("--cleared", action="store_true")
    ap.add_argument("--corrupt", action="store_true")
    ap.add_argument("--v1", action="store_true",
                    help="write the pre-2026-09-04 format, which has no uid field")
    ap.add_argument("--wrong-uid", action="store_true",
                    help="carry another board's uid, as a copied record would")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--ip", default=getattr(cfg, "BOARD_IP", ""))
    ap.add_argument("--port", default="56865")
    ap.add_argument("--also-unsigned", type=int, default=0,
                    help="add a second, unsigned record at this generation")
    ap.add_argument("--also-cleared", action="store_true",
                    help="make that second record a factory reset")
    ap.add_argument("--seconds", type=int, default=10)
    args = ap.parse_args()

    boot_bin = Path(cfg.BOOT_REPO) / "Debug" / "open_plc_cube_ide.bin"
    if not boot_bin.exists():
        Fail("no bootloader .bin at %s - build it first" % boot_bin)
        return 2
    cli = get_programmer_cli()

    image = boot_bin.read_bytes()
    if len(image) > OWNER_OFFSET:
        Fail("the bootloader is %s B and would run into the owner area at %s B"
             % (format(len(image), ",d"), format(OWNER_OFFSET, ",d")))
        return 2

    if args.restore:
        Section("restoring the plain bootloader")
        out = image
    else:
        Section("building bootloader + owner record")

        # format_ver: 2 normally, 99 for --corrupt and 1 for --v1, both of
        # which the scanner must reject rather than try to interpret.
        ver = 99 if args.corrupt else (1 if args.v1 else OWNER_FORMAT_VER)
        flags = 1 if args.cleared else 0

        # A v2 record only counts on the board whose uid it carries. Cleared
        # records are exempt (they assert nothing about which board), and a v1
        # record has no field to put it in.
        uid = b""
        if ver == OWNER_FORMAT_VER and not args.cleared:
            if args.wrong_uid:
                uid = bytes(range(1, UID_LEN + 1))
                print("  uid: another board's (%s)" % uid.hex())
            else:
                uid = board_uid(args.ip, args.port)
                if uid is None:
                    Fail("could not read this board's uid over TCP at %s:%s -- "
                         "it has to be in the bootloader and reachable" % (args.ip, args.port))
                    return 2
                print("  uid: this board's (%s)" % uid.hex())

        # root_pubkey: all zero in a cleared record. Otherwise a real key when
        # one is given -- needed to set up a board for the setowner cases, where
        # the next record has to be signed by whoever this record names -- and a
        # recognisable pattern when it is not, for the cases where the key's
        # value never matters.
        key_hex, filler = "", None
        if not args.cleared:
            if args.key:
                if len(args.key) != 128:
                    Fail("--key needs 128 hex chars, got %d" % len(args.key))
                    return 2
                key_hex = args.key
                print("  root_pubkey: %s..." % args.key[:32])
            else:
                filler = 0xAA
        rec = record(args.generation, ver, flags, key_hex, filler, uid)

        print("  type 'O', slots 5, format_ver %d, generation %d, flags %d"
              % (ver, args.generation, flags))

        # 0xFF for the gap, so the unused part of the area still reads as erased.
        total = RECORD_SIZE * (2 if args.also_unsigned > 0 else 1)
        out = bytearray(b"\xFF" * (OWNER_OFFSET + total))
        out[0:len(image)] = image
        out[OWNER_OFFSET:OWNER_OFFSET + RECORD_SIZE] = rec

        if args.also_unsigned > 0:
            # A second record with a HIGHER generation and no signature.
            #
            # Without --also-cleared this is what an attacker able to append
            # would write to take a claimed board over, and the bootloader must
            # reject it: authority comes from the chain, not from being the
            # highest generation present.
            #
            # With --also-cleared it is a factory reset, which is legitimately
            # unsigned -- gated by a physical action instead. Same shape,
            # opposite verdict, which is exactly why both are worth having.
            if args.also_cleared:
                att = record(args.also_unsigned, OWNER_FORMAT_VER, 1, "", None)
                print("  plus a CLEARED record at generation %d (should apply)" % args.also_unsigned)
            else:
                att = record(args.also_unsigned, OWNER_FORMAT_VER, 0, "", 0xBB, uid)
                print("  plus an UNSIGNED record at generation %d (should be rejected)"
                      % args.also_unsigned)
            out[OWNER_OFFSET + RECORD_SIZE:OWNER_OFFSET + 2 * RECORD_SIZE] = att
        out = bytes(out)

    tmp = Path(tempfile.gettempdir()) / "bootloader_with_owner.bin"
    tmp.write_bytes(out)
    print("  image: %s bytes" % format(len(out), ",d"))

    Section("flashing")
    assert_target_reachable(cli)
    open_ports = open_log_ports(cfg.LOG_PORTS)
    text, _ = run_capture([cli, "-c", "port=SWD", "mode=UR", "-w", str(tmp), "0x08000000", "-rst"])
    for line in text.splitlines():
        if re.search(r"Download|verified|Error|Reset", line):
            print("  " + line)

    all_text = "\n".join(read_log_ports(open_ports, args.seconds).values())

    Section("boot log")
    # "PUBLISHED root" belongs in this list: it is the line that says whether the
    # board is still trusting a key everybody has, which is the whole subject
    # here. Leaving it out once made a correct result look like a missing warning.
    for line in all_text.splitlines():
        if INTERESTING.search(line):
            print("    | " + line)

    Section("result")
    if "Owner slot:" not in all_text:
        Fail("no 'Owner slot:' line - is this bootloader new enough?")
        return 1
    Ok("flashed; read the line above against what this record was meant to be")
    return 0


if __name__ == "__main__":
    sys.exit(main())
