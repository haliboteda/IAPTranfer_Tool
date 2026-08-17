# fakeboard — IAPTool's pre-transfer decisions, without a board

`fake_board.py` is the stand-in device. Two suites drive the real `IAPTool.exe`
against it, both covering decisions the tool makes *before* any firmware moves:

| Suite | Case | Covers | What it checks |
|---|---|---|---|
| `run-cases.ps1` | K1–K6 | C8 | which signing key this board will accept |
| `run-downgrade.ps1` | **DG1** | **C6** | whether an older image is refused |

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

```powershell
.\run-cases.ps1              # all six
.\run-cases.ps1 -Keep        # keep the scratch directory to inspect logs
```

Needs `python` and `go` on PATH. Builds `IAPTool.exe` if it is missing.
Also run as step A10 of `tools\selfcheck.ps1`.

## Two things the runner has to do that are not obvious

**It runs a copy of IAPTool from a scratch directory.** IAPTool resolves
`local_config.json` and its fallback `keys/` directory relative to *its own
executable*, not the working directory. So the three "the host has no private
key" cases cannot be produced by simply omitting `--key` — the tool falls back
to the `signing_key` in the repo's config and signs anyway. The first version of
this script did exactly that and reported three passes that tested nothing.

**The scratch `local_config.json` is written without a BOM.** PowerShell 5.1's
`Set-Content -Encoding utf8` adds one, Go's `json.Unmarshal` rejects it, and
IAPTool exits before doing anything — which reads as a broken tool rather than a
broken config.

## Keys

The "good" key is parsed out of `$BOOT_REPO/IAPServer/keys/fw_pubkey.inc`, the
same file the bootloader compiles in, so a key rotation cannot leave a stale
copy here. The "bad" key is generated per run by `IAPTool genkey`, so nothing
needs committing and openssl is not required.

---

# DG1 · the downgrade guard

## What this covers

Requirement **C6**: an image older than what the device runs is refused, and
refusing it leaves the installed app alone. Five cases:

| Case | Image version | `--downgrade` | Expected |
|---|---|---|---|
| `refuse-older` | older | `refuse` | refuses, **no `flash` sent** |
| `allow-older` | older | `allow` | proceeds, image lands |
| `ask-no-console` | older | `ask` | refuses (no terminal to ask at), **no `flash` sent** |
| `same-version` | equal | `refuse` | proceeds — not a downgrade |
| `newer` | newer | `refuse` | proceeds |

The board's reported version comes from `fake_board.py --fwver N`; the three
image versions are encoded at run time by `IAPTool version`, so the packed-byte
layout is never copied into this suite.

## Two assertions per case, and the second one is the point

Each case checks **what IAPTool printed** *and* **whether the board was ever
sent a `flash` command**. A tool that printed `Downgrade refused` and then
uploaded anyway would pass a log-only check — and C6's real claim is that the
installed app is untouched, which only the board's own log can show. The
positive cases additionally require `IMAGE FULLY RECEIVED`: `flash` being sent
proves the guard let go, not that the transfer survived.

Both outcomes of that assertion occur in every run (three cases require the
`flash` command, two require its absence), so a predicate that had degenerated
into always-true or always-false could not pass the suite.

## Two cases that are easy to get wrong

**`same-version` is the boundary.** The comparison is `local >= remote`; an
off-by-one there would lock out re-flashing the same build, which is the most
common thing anybody does.

**`ask-no-console` gets a piped stdin on purpose.** `--downgrade=ask` behaves
differently depending on whether stdin is a console, and that branch exists
because reading stdin from an IDE returns EOF immediately and used to be
reported as "declined by operator" when nobody had been asked. Forcing the pipe
makes the case deterministic instead of dependent on how the script was started.

## Why it is not a hardware test

Every branch under test is in IAPTool (`auth.go`, `confirmDowngradeIfNeeded`).
The device contributes exactly one thing: its answer to `getversion`. Setting
those cases up on hardware would mean flashing a real older app first. The
device side of "a rejected upload does not damage the installed app" is case
**G1**, against real hardware.

## Running

```powershell
.\run-downgrade.ps1              # all five
.\run-downgrade.ps1 -Keep        # keep the scratch directory to inspect logs
```

Also run as step A12 of `tools\selfcheck.ps1`.
