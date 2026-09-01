# IAP Bootloader Host Test

Runs the *real* bootloader auth/crypto source
(`open_plc_cube_ide/IAPServer/sha256.c`, `iap_keyderive.c`, `iap_auth.c`)
natively on a PC, against a fake STM32 HAL (`stubs/`), instead of only being
testable by flashing real hardware.

Out of scope on purpose: `IAP_server.c`'s command parser and the USB/TCP/
Flash stack it needs. This harness only covers the auth/crypto core.

## Build & run

Needs any C11 compiler (gcc or clang) on `PATH`.

```
./build.sh              # bash / Git Bash / WSL
CC=clang ./build.sh      # force a specific compiler
```

```powershell
```

Both compile `test_main.c` + the stubs + the three real `IAPServer` sources
into `iap_hosttest[.exe]` and run it. Exit code is `0` iff every check
passes.

## What's checked

- `sha256_selftest()` - the crypto primitives against FIPS 180-4 / RFC 4231
  vectors.
- Device-key derivation is deterministic per UID and different for
  different UIDs.
- `getuid`/discovery hex format (`iap_keyderive_get_machine_id_hex`).
- **Golden cross-language vector**: expected nonce, device key, and HMAC for
  a fixed (UID, counter, tick, message) were computed independently in Go
  using `IAPTranfer_Tool/iapcrypto`. The bootloader's C code reproducing
  those exact values proves the two implementations are wire-compatible,
  not just each internally self-consistent.
- Replay protection (same nonce/hmac rejected the second time).
- Nonce TTL expiry (correctly-signed but >30s-old nonce rejected).
- An HMAC signed with a *different* device's key is rejected.

## If this harness needs to change

If the wire format changes (nonce layout, HMAC construction, key
derivation), regenerate the golden vector in Go against the *new*
`iapcrypto` code and update the expected strings in
`test_golden_cross_language_vector()` - don't hand-edit them.
