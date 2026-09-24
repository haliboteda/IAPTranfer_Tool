# IAP Bootloader Host Test

Case **T1-16**. Runs the *real* bootloader certificate and auth source
(`open_plc_cube_ide/IAPServer/`: `sha256.c`, `iap_keyderive.c`, `iap_cert.c`,
`fw_verify.c` + the vendored micro-ecc, `iap_auth.c`) natively on a PC against
a fake STM32 HAL and a fake owner slot (`stubs/`), instead of only being
testable by flashing real hardware.

Out of scope on purpose: `IAP_server.c`'s command parser and the USB/TCP/Flash
stack it needs. This harness covers the certificate/auth core.

## Build & run

Needs any C11 compiler. Resolution order: `$CC`, then `HOST_CC` from
`config/machine.py`, then `gcc` or `clang` on `PATH`.

```
python build.py
```

Exit code is `0` iff every check passes.

## What's checked

- `sha256_selftest()` — the crypto primitives against FIPS 180-4 vectors.
- Certificate layout: 132 bytes, `leaf_pubkey` at 0, `serial` at 64 as a
  little-endian `uint32`, `root_sig` at 68, no padding.
- `iap_cert_verify()` accepts a certificate its root signed — delegated and
  self-signed alike — and rejects one signed by any other root.
- Tampering with `leaf_pubkey`, `serial`, or `root_sig` breaks verification.
- `iap_cert_verify_image()` needs **both** halves: an uncertified leaf is
  rejected even when it really did sign the image, and a certified leaf does
  not vouch for an image somebody else signed.
- Changing the trusted root retroactively invalidates firmware certified by
  the old one — the property that makes `setowner` mean anything.
- A full challenge-response: the nonce is the 16 bytes the RNG handed over (the
  stub is told which, so the golden signature still applies), acceptance, replay
  rejection, expiry past `IAP_AUTH_NONCE_TTL_MS`, an answer with no challenge
  behind it, an uncertified signer, and a certified certificate carrying
  somebody else's signature.
- An RNG that cannot deliver issues no challenge at all, and leaves no earlier
  nonce still accepting answers.

## The golden vectors

Every certificate and signature in `golden_vectors.h` was produced by the
**shipping PC tool** (`IAPTool cert` / `signraw` / `genkey`), not by a second
implementation written for the test. A passing T1-16 therefore means the
bootloader's C code and the Go tool agree on the wire format, rather than each
being internally consistent.

Regenerate only when the wire format changes — the certificate layout, what
the root signature covers, or the nonce construction:

```
python gen_vectors.py
```

Never hand-edit `golden_vectors.h`. The keys used to make it are temporary and
are deleted with the run.
