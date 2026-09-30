# IAPTranfer_Tool
For OpenPLC transfer bin file

中文：[README.zh-CN.md](README.zh-CN.md)

## Building from source

Two programs come out of this repository: `IAPTool` (signs and uploads
firmware) and `PortTool` (the port test panel). Both need only Go 1.23 or
newer. The first build downloads three modules, so it needs network access.

Run from the repository root:

```
go build -o IAPTool .                     # IAPTool for this machine
go build -o PortTool ./cmd/porttool       # PortTool for this machine
```

On Windows name the outputs `IAPTool.exe` and `PortTool.exe`.

To build for another system, set `GOOS` / `GOARCH` (no C compiler needed):

```
GOOS=linux   GOARCH=amd64 CGO_ENABLED=0 go build -o PortTool ./cmd/porttool
GOOS=darwin  GOARCH=arm64 go build -o PortTool ./cmd/porttool      # Apple silicon
GOOS=windows GOARCH=amd64 go build -o PortTool.exe ./cmd/porttool
```

PortTool's web page is compiled into the executable. Its test plans are not:
copy `TestCase/plans/*.json` into a `plans/` folder beside `PortTool`.

On Linux the user running PortTool needs access to the serial ports, usually
by being in the `dialout` group.

`compile_tool.sh` and `build.py` are the maintainers' release scripts: they
also build the firmware and fill the Arduino board package, which needs the
other OpenPLC repositories. They are not needed to build the two tools.

## Usage

```
IAPTool cdc    <file.bin> <port>       [--key=<key.pem>] [--cert=<cert.txt>]
IAPTool ether  <file.bin> <ip>         [--key=<key.pem>] [--cert=<cert.txt>]
IAPTool flashboot <boot.bin> <ip>      --key=<owner.pem>
IAPTool sign   <file.bin> [<key.pem>]  [--key=<key.pem>] [--out=<prefix>]
IAPTool genkey [<name>]
IAPTool pubkey [<key.pem>]
IAPTool cert   [<leafPubHex>]          [--key=<root.pem>]

IAPTool getowner <ip>
IAPTool takeown  <ip> --key=<owner.pem>
IAPTool setowner <ip> --current-key=<owner.pem> --new-key=<next.pem>
```

## Firmware signing

The bootloader only executes an image whose ECDSA P-256 signature verifies
against the `fw_public_key[64]` baked into it (`IAPServer/fw_pubkey.c`), so
every image has to be signed before it can be flashed. Signing is built in
here -- no `openssl`, no shell scripts, nothing to `chmod +x`.

```sh
# Nothing is written to disk; the image is signed in memory and sent straight
# to the device.
IAPTool cdc app.bin COM5 --key=fw_signing_key.pem
```

`sign` writes `<name>.sha256`, `<name>.size` and `<name>.sig` (raw 64-byte
r||s) for anyone who needs the signature as a file, but uploading does not use
them: the upload also has to sign a fresh challenge from the board, which needs
the private key itself.

The tool does not track firmware versions and does not compare them. An image
that verifies against the key the board trusts is flashed, whatever it
contains -- versioning an application is the author's business.

### Where the key comes from

Resolution order:

1. `--key=<path>` on the command line
2. `"signing_key"` in `local_config.json` (write an absolute path)
3. `<user config dir>/openplc/keys/fw_signing_key.pem` (`os.UserConfigDir()`;
   `%AppData%` on Windows) -- survives tool package upgrades
4. `keys/fw_signing_key.pem` next to the executable
5. Uploads only: `keys/published_root.TEST_ONLY.pem` next to the executable,
   the published root key `compile_tool.sh` ships. Only an unclaimed board
   accepts it; every use prints a warning

The Arduino IDE passes no key, so steps 3-5 are what it relies on.
`local_config.json` and `keys/` are looked up relative to the **executable**,
not the current directory.

## Uploading without the root key

One person with one key needs nothing below: their key is the root, it
certifies itself, and everything already works.

A team where one administrator holds the root does need it. Each colleague
keeps their own key; the administrator issues a certificate saying that key is
authorised, and the root private key never leaves the administrator's machine.

```sh
# On the colleague's machine
IAPTool genkey fw_signing_key               # their own key; move it to step 3 above
IAPTool pubkey                              # 128 hex characters -- send these

# On the administrator's machine
IAPTool cert <those 128 hex characters> --key=root.pem > colleague.cert

# Back on the colleague's machine: save it beside the key it covers
#   <user config dir>/openplc/keys/fw_signing_key.pem.cert
IAPTool ether app.bin 192.168.1.50           # nothing else changes
```

A certificate lives at `<the key it covers>.cert`, so a key and its
certificate cannot be paired up wrongly, and the Arduino IDE finds it with no
configuration for the same reason it finds the key. `--cert=<file>` overrides.

Revoking one colleague is `IAPTool revoke --leaf=<their pubkey>`: that leaf is
refused from its next upload on, while firmware it already installed keeps
running (`IAPTool getapprevoked` finds those boards). Handing the board to a new
root (`IAPTool setowner`) retires every certificate the old root issued,
including on firmware already installed. See
`OpenPLC_Docs/docs/modules/M2-ownership.md`.

### Mismatch is caught before the transfer

Before sending anything, the tool asks the bootloader (`getpubkey`) which root
it verifies against, and checks that the certificate it is about to present was
issued by that root. For a self-signed certificate this is the same question as
"is my key the board's key", so there is one check rather than two paths.

A mismatch stops the upload immediately with both fingerprints printed,
instead of transferring the whole image and having the board reject it at the
end. A bootloader too old to know `getpubkey` answers `Unknown command`; the
check is then skipped with a warning and the upload proceeds as before.

### Generating keys

To rotate the signing key, run `IAPServer/keys/rotate_keys.sh` -- it drives
the command below, distributes every copy and takes a backup first. The piece
is also available on its own:

```sh
IAPTool genkey my_release_key > fw_pubkey.inc
```

`genkey` writes `my_release_key.pem` (private, mode 0600) and prints the body
of `IAPServer/keys/fw_pubkey.inc` on stdout. That file is `#include`d by the
firmware, so the bootloader must be rebuilt and re-flashed over ST-Link before
the new key takes effect. Keep the private key offline; it never goes on a
device.

Keys are interchangeable with `openssl` in both directions -- `genkey` emits
standard SEC1 PEM, and `sign` accepts SEC1 or PKCS#8.

## Board ownership

A board leaves the factory trusting the signing key published with this
project, which means anyone can sign firmware it will run. Claiming it binds
it to a key of your own, and from then on nothing else will start.

```sh
IAPTool genkey owner                 # writes owner.pem - keep it offline
IAPTool getowner 192.168.0.30        # which key does this board trust?
IAPTool takeown  192.168.0.30 --key=owner.pem
IAPTool setowner 192.168.0.30 --current-key=owner.pem --new-key=next.pem
```

## Replacing the bootloader

`flashboot` writes a new bootloader into sector 0 without an ST-Link, carrying
the owner records across so the board stays claimed.

```sh
IAPTool flashboot boot.bin 192.168.0.30 --key=owner.pem
```

The key has to be the owner root itself: the board checks a bootloader image
against the root, not against a leaf certificate. An unclaimed board has no
root to check, so it demands BOOT0 held through its current boot instead.

**Do not cut power during it.** The board is running out of the sector being
rewritten; an interruption leaves it unable to boot, and only an ST-Link gets
it back.

`takeown` is refused unless BOOT0 was held through the board's current boot.
The first claim carries no signature -- there is no owner yet to produce one --
so physical presence is the only gate there can be. It also refuses to fall
back to the signing key in `local_config.json`: claiming a board with the wrong
key can only be undone by reflashing the bootloader over ST-Link, because the
owner records live in the bootloader's own flash sector.

`setowner` needs no button. It is signed by the key the board trusts today, so
a handover can be done over the network, and a stolen record cannot take a
board over -- the board checks the signature, not the generation number.

## Reclaiming revocation slots

The owner area holds 96 revocations and only ever appends, so the only way to
get slots back is to erase the flash sector they live in. The boot log starts
saying so with 8 left.

```sh
IAPTool setowner 192.168.0.30 --current-key=owner.pem --new-key=next.pem --wipe
```

`--wipe` hands the board over AND leaves the area holding nothing but the new
record. The board erases and rewrites its own sector to do it, so it resets,
and **a power cut during the erase means holding BOOT0 through a reset and
re-flashing over USB DFU** -- the same recovery `flashboot` needs. Plain
`setowner` appends one record and carries none of that risk; the board never
decides to wipe on its own.

Changing the root *without* `--wipe` retires every leaf the old root issued,
so they no longer need revoking one by one -- but it does not free the slots
those revocations already occupy.
