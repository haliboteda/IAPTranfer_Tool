# fakeboard — IAPTool's pre-transfer decisions, without a board

`fake_board.py` is the stand-in device. `run_cases.py` drives the real
`IAPTool.exe` against it, covering the decision the tool makes *before* any
firmware moves:

| Suite | Case | Covers | What it checks |
|---|---|---|---|
| `run_cases.py` | K1–K6 | C8 | which signing key this board will accept |

---

# K1–K6 · the key-match decision

## What this covers

Before IAPTool sends a single byte of firmware it asks the device `getpubkey`
and decides whether the key it would sign with is one the device will accept.
That decision has six outcomes. All six are checked here.

| Case | Board answers `getpubkey` | Host has | Expected |
|---|---|---|---|
| `key-match` | the key IAPTool signs with | private key | `Signing key matches this board` |
| `key-mismatch` | a different key | private key | refuses: `verifies against a different signing key` |
| `old-bootload` | `Unknown command` | private key | proceeds: `skipping key match check` |
| `sig-match` | the key the `.sig` was made with | only a `.sig` | `Signature verifies against this board` |
| `sig-mismatch` | a different key | only a `.sig` | refuses: `does not verify against this board` |
| `nothing` | any | neither | refuses: `no signing key found and no signature` |

## Why it is not a hardware test

Each row differs only in which key the *bootloader was compiled with*. On real
hardware, moving between rows means rebuilding and reflashing the bootloader
with a different key — six ST-Link rounds to check one branch of host-side
logic. Here it is a command-line argument.

`fake_board.py` verifies nothing at all. It answers protocol commands with fixed
strings. **What is under test is IAPTool**; the device's own signature checking
is case S1, against real hardware.

## Running

```
python run_cases.py              # all six
python run_cases.py --keep       # keep the scratch directory to inspect logs
```

Needs `python` and `go` on PATH. Builds `IAPTool.exe` if it is missing.
Also run as steps K1-K6 of `tools/selfcheck.py`.

## Two things the runner has to do that are not obvious

**It runs a copy of IAPTool from a scratch directory.** IAPTool resolves
`local_config.json` and its fallback `keys/` directory relative to *its own
executable*, not the working directory. So the three "the host has no private
key" cases cannot be produced by simply omitting `--key` — the tool falls back
to the `signing_key` in the repo's config and signs anyway. The first version of
this script did exactly that and reported three passes that tested nothing.

**The scratch `local_config.json` is written without a BOM.** A BOM-writing editor's
`Set-Content -Encoding utf8` adds one, Go's `json.Unmarshal` rejects it, and
IAPTool exits before doing anything — which reads as a broken tool rather than a
broken config.

## Keys

The "good" key is parsed out of `$BOOT_REPO/IAPServer/keys/fw_pubkey.inc`, the
same file the bootloader compiles in, so a key rotation cannot leave a stale
copy here. The "bad" key is generated per run by `IAPTool genkey`, so nothing
needs committing and openssl is not required.

---
