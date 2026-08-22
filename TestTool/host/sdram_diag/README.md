# SDRAM data-bus diagnostic captures

Raw console captures from `tools/run-sdram-diag.ps1`, kept because they are
measurements of one physical board at one moment and cannot be reproduced once
that board is repaired or replaced.

The firmware side is `open_plc_cube_ide/Core/Src/sdram_diag.c` (serial commands
`sdramdiag` and `sdramlive`). What the numbers mean, and what has been ruled
out, lives in `open_plc_cube_ide/docs/test/MEASUREMENTS.md` -- not here. **This
directory holds evidence, not conclusions.**

| file | board | what it caught |
|---|---|---|
| `2026-08-21-bridge-run1.txt` | Bridge 1436_01_SCHAE-BR, the only sample | First instrumented run. `FMC_D1` (bit1, `PD15`) 48.0 % word errors, every other data line exactly 0. Writes of 1 survive 90.7 %, writes of 0 only 13.4 % -- the net reads high far more often than it should. Release time 58 % of the six neighbours on the same GPIO port. |

⚠️ Run 1 predates measurement 4 (the float test), which was written the same day
to separate "less capacitance" from "leak to a rail". **Nothing in this
directory has run measurement 4 yet.**

Naming: `<date>-<board>-<runN>.txt`. Never overwrite a capture -- add one.
