"""Regenerates golden_vectors.h for the H2 host harness.

Every certificate and signature the C tests check is produced here by the
real shipping tool (IAPTool cert / signraw / genkey) rather than by a second
implementation written for the test. A passing H2 therefore proves the
bootloader's C code and the PC tool agree on the wire format -- not just that
each is internally consistent.

    python gen_vectors.py [--iaptool <path>]

The output is committed. Run this again only when the wire format changes
(certificate layout, what the root signature covers, the nonce construction);
never hand-edit golden_vectors.h.

The keys are thrown away with the temporary directory: nothing here is meant
to be reused, and a key that outlives the run is a key someone can mistake for
a real one.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import EXE, cfg  # noqa: E402

# Fixed inputs the C side reproduces exactly. The UID/tick/counter triple is
# what makes the nonce predictable: iap_auth_issue_challenge() builds it as
# counter(4,LE) || UIDW0(4,LE) || tick(4,LE) || 0(4).
UID0, UID1, UID2 = 0x01234567, 0x89ABCDEF, 0xDEADBEEF
TICK = 5000
COUNTER = 1
AUTH_MSG = b"flash 1024 deadbeef abcd1234"
IMAGE_BLOB = b"golden image bytes for the H2 harness"

HEX_LINE = re.compile(r"^[0-9a-f]+$")


def run_tool(iaptool, *args):
    """Runs IAPTool and returns the one hex line it printed.

    IAPTool writes progress through its logger, so the payload is picked out
    by shape rather than by position -- a new log line must not silently
    become the returned value.
    """
    proc = subprocess.run([str(iaptool), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise SystemExit("IAPTool %s failed:\n%s%s" % (" ".join(args), proc.stdout, proc.stderr))
    hexes = [ln.strip() for ln in proc.stdout.splitlines()
             if HEX_LINE.match(ln.strip()) and len(ln.strip()) >= 64]
    if len(hexes) != 1:
        raise SystemExit("IAPTool %s printed %d hex lines, expected 1:\n%s"
                         % (" ".join(args), len(hexes), proc.stdout))
    return hexes[0]


def genkey(iaptool, path):
    """Creates a key and returns its public half as 128 hex characters.

    genkey prints the fw_pubkey.inc body (C initialiser lines), which is the
    only place the raw point is exposed, so it is parsed back here. It takes a
    name and appends ".pem" itself, so the suffix is stripped off here.
    """
    proc = subprocess.run([str(iaptool), "genkey", str(path.with_suffix(""))], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise SystemExit("IAPTool genkey failed:\n%s%s" % (proc.stdout, proc.stderr))
    byte_vals = re.findall(r"0x([0-9a-fA-F]{2})", proc.stdout)
    if len(byte_vals) != 64:
        raise SystemExit("genkey printed %d key bytes, expected 64" % len(byte_vals))
    return "".join(b.lower() for b in byte_vals)


def nonce_bytes():
    out = bytearray()
    for v in (COUNTER, UID0, TICK, 0):
        out += v.to_bytes(4, "little")
    return bytes(out)


def c_array(name, data):
    lines = ["static const uint8_t %s[%d] = {" % (name, len(data))]
    for i in range(0, len(data), 12):
        chunk = ", ".join("0x%02x" % b for b in data[i:i + 12])
        lines.append("\t%s," % chunk)
    lines.append("};")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iaptool", default=str(Path(cfg.TOOL_REPO) / "Output" / ("IAPTool" + EXE)))
    args = ap.parse_args()

    iaptool = Path(args.iaptool)
    if not iaptool.exists():
        raise SystemExit("IAPTool not found at %s -- build it first (go build -o Output/ .)" % iaptool)

    with tempfile.TemporaryDirectory(prefix="h2-vectors-") as tmp:
        tmp = Path(tmp)
        root_key, leaf_key, foreign_key = tmp / "root.pem", tmp / "leaf.pem", tmp / "foreign.pem"

        root_pub = genkey(iaptool, root_key)
        leaf_pub = genkey(iaptool, leaf_key)
        foreign_pub = genkey(iaptool, foreign_key)

        cert_self = run_tool(iaptool, "cert", "--key=%s" % root_key)
        cert_delegated = run_tool(iaptool, "cert", leaf_pub, "--key=%s" % root_key)
        cert_foreign = run_tool(iaptool, "cert", "--key=%s" % foreign_key)

        blob_hex = IMAGE_BLOB.hex()
        image_sig_leaf = run_tool(iaptool, "signraw", blob_hex, str(leaf_key))
        image_sig_root = run_tool(iaptool, "signraw", blob_hex, str(root_key))
        image_sig_foreign = run_tool(iaptool, "signraw", blob_hex, str(foreign_key))

        auth_hex = (nonce_bytes() + AUTH_MSG).hex()
        auth_sig_leaf = run_tool(iaptool, "signraw", auth_hex, str(leaf_key))
        auth_sig_foreign = run_tool(iaptool, "signraw", auth_hex, str(foreign_key))

    body = [
        "/*",
        " * Golden vectors for the H2 host harness -- GENERATED, do not hand-edit.",
        " * Regenerate with: python gen_vectors.py",
        " *",
        " * Produced by the shipping IAPTool (cert / signraw / genkey), so a passing",
        " * test means the bootloader's C code and the PC tool agree on the wire",
        " * format. The private keys were temporary and no longer exist.",
        " */",
        "",
        "#ifndef H2_GOLDEN_VECTORS_H_",
        "#define H2_GOLDEN_VECTORS_H_",
        "",
        "#include <stdint.h>",
        "",
        "/* The device this harness pretends to be. */",
        "#define GOLDEN_UID0 0x%08XU" % UID0,
        "#define GOLDEN_UID1 0x%08XU" % UID1,
        "#define GOLDEN_UID2 0x%08XU" % UID2,
        "#define GOLDEN_TICK %uU" % TICK,
        "",
        '#define GOLDEN_AUTH_MSG "%s"' % AUTH_MSG.decode(),
        "",
        c_array("golden_root_pub", bytes.fromhex(root_pub)),
        "",
        c_array("golden_leaf_pub", bytes.fromhex(leaf_pub)),
        "",
        c_array("golden_foreign_pub", bytes.fromhex(foreign_pub)),
        "",
        "/* Simple mode: the root certifies its own key. */",
        c_array("golden_cert_self", bytes.fromhex(cert_self)),
        "",
        "/* The root certifies a separate leaf key. */",
        c_array("golden_cert_delegated", bytes.fromhex(cert_delegated)),
        "",
        "/* Structurally perfect, signed by a root this board does not trust. */",
        c_array("golden_cert_foreign", bytes.fromhex(cert_foreign)),
        "",
        c_array("golden_image_blob", IMAGE_BLOB),
        "",
        c_array("golden_image_sig_leaf", bytes.fromhex(image_sig_leaf)),
        "",
        c_array("golden_image_sig_root", bytes.fromhex(image_sig_root)),
        "",
        c_array("golden_image_sig_foreign", bytes.fromhex(image_sig_foreign)),
        "",
        "/* ECDSA over sha256(nonce || GOLDEN_AUTH_MSG) for the nonce the stubbed",
        " * HAL makes iap_auth_issue_challenge() produce. */",
        c_array("golden_auth_sig_leaf", bytes.fromhex(auth_sig_leaf)),
        "",
        c_array("golden_auth_sig_foreign", bytes.fromhex(auth_sig_foreign)),
        "",
        "#endif /* H2_GOLDEN_VECTORS_H_ */",
        "",
    ]

    out = HERE / "golden_vectors.h"
    out.write_text("\n".join(body), encoding="utf-8")
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
