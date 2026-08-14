# IAPTranfer_Tool
For OpenPLC transfer bin file

## Usage

```
IAPTool cdc    <file.bin> <port>       [--key=<key.pem>] [--version=N]
IAPTool ether  <file.bin> <ip>         [--key=<key.pem>] [--version=N]
IAPTool sign   <file.bin> [<key.pem>]  [--key=<key.pem>] [--version=N] [--out=<prefix>]
IAPTool genkey [<name>]
```

## Firmware signing

The bootloader only executes an image whose ECDSA P-256 signature verifies
against the `fw_public_key[64]` baked into it (`IAPServer/fw_pubkey.c`), so
every image has to be signed before it can be flashed. Signing is built in
here -- no `openssl`, no shell scripts, nothing to `chmod +x`.

Two ways to do it:

```sh
# Sign at flash time. Nothing is written to disk; the image is signed in
# memory and sent straight to the device.
IAPTool cdc app.bin COM5 --key=fw_signing_key.pem --version=7

# Or sign separately, e.g. on an offline release machine, then hand the
# .bin + .sig to whoever flashes it.
IAPTool sign app.bin fw_signing_key.pem --version=7
IAPTool cdc app.bin COM5
```

`sign` writes `<name>.sha256`, `<name>.size`, `<name>.sig` (raw 64-byte
r||s), and `<name>.version` when `--version` is given. Without `--key`, the
`cdc`/`ether` modes read those sibling files, so an image signed earlier or
on another machine still works unchanged.

`--version` is optional. It only drives the operator downgrade warning: the
tool asks the device for its installed version and prompts before pushing an
older one. Without it, no comparison is possible and the flash proceeds.

### Where the key comes from

Resolution order:

1. `--key=<path>` on the command line
2. `"signing_key"` in `local_config.json`
3. `keys/fw_signing_key.pem` next to the executable
4. nothing - then a sibling `<file>.sig` is required, and if that is missing
   too the tool stops and says so

Step 3 is what makes the Arduino IDE work with no configuration: the key
travels in the tool's own directory, so `platform.txt` needs no path passed
in and no per-machine setup. Both `local_config.json` and `keys/` are looked
up relative to the **executable**, not the current directory.

### Key mismatch is caught before the transfer

Before sending anything, the tool asks the bootloader (`getpubkey`) which
public key it verifies against, and compares:

- signing locally → the local key's public half must equal the board's
- using a `.sig` → the signature must verify against the board's public key,
  which also proves the image has not changed since it was signed

A mismatch stops the upload immediately with both fingerprints printed,
instead of transferring the whole image and having the board reject it at the
end. A bootloader too old to know `getpubkey` answers `Unknown command`; the
check is then skipped with a warning and the upload proceeds as before.

### Generating keys

To replace both secrets at once, run `IAPServer/keys/rotate_keys.sh` -- it
drives the two commands below, distributes every copy and takes a backup
first. The pieces are also available on their own:

```sh
IAPTool genkey my_release_key > fw_pubkey.inc
IAPTool genpw > iap_fixed_password.txt
```

`genkey` writes `my_release_key.pem` (private, mode 0600) and prints the body
of `IAPServer/keys/fw_pubkey.inc` on stdout; `genpw` prints a fresh
`iap_fixed_password.txt`. Both files are `#include`d by the firmware, so the
bootloader must be rebuilt and re-flashed over ST-Link before either takes
effect. Keep the private key offline; it never goes on a device.

Keys are interchangeable with `openssl` in both directions -- `genkey` emits
standard SEC1 PEM, and `sign` accepts SEC1 or PKCS#8.