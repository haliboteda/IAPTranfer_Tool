/*
 * E6 / M4 acceptance: the FMC_RESERVED_* pin table in the Arduino variant.
 *
 * Compile-only. Nothing here is meant to run on a board -- the whole check is
 * in the static_asserts, which fail the build if the table is wrong.
 *
 * What it does NOT check: whether the table agrees with the bootloader's
 * fmc.c, which is where the pins are really configured. That is a cross-repo
 * comparison and belongs to case P2 (tools/check-mirror-sync.ps1, anchor
 * "FMC pin map"). This file only proves the variant header is internally
 * consistent and says what it claims to say.
 */

void setup() {
  // 1. Every name resolves to a pin number.
  const int reserved[] = {
    FMC_RESERVED_D0,  FMC_RESERVED_D1,  FMC_RESERVED_D2,  FMC_RESERVED_D3,
    FMC_RESERVED_D4,  FMC_RESERVED_D5,  FMC_RESERVED_D6,  FMC_RESERVED_D7,
    FMC_RESERVED_D8,  FMC_RESERVED_D9,  FMC_RESERVED_D10, FMC_RESERVED_D11,
    FMC_RESERVED_D12, FMC_RESERVED_D13, FMC_RESERVED_D14, FMC_RESERVED_D15,
    FMC_RESERVED_A0,  FMC_RESERVED_A1,  FMC_RESERVED_A2,  FMC_RESERVED_A3,
    FMC_RESERVED_A4,  FMC_RESERVED_A5,  FMC_RESERVED_A6,  FMC_RESERVED_A7,
    FMC_RESERVED_A8,  FMC_RESERVED_A9,  FMC_RESERVED_A10, FMC_RESERVED_A11,
    FMC_RESERVED_A12,
    FMC_RESERVED_BA0, FMC_RESERVED_BA1, FMC_RESERVED_NBL0, FMC_RESERVED_NBL1,
    FMC_RESERVED_SDCLK, FMC_RESERVED_SDCKE0, FMC_RESERVED_SDNE0,
    FMC_RESERVED_SDNRAS, FMC_RESERVED_SDNCAS, FMC_RESERVED_SDNWE,
  };

  // 2. The count macro agrees with the list above: catches a pin dropped from
  //    one of the two places without the other noticing.
  static_assert(sizeof(reserved) / sizeof(reserved[0]) == FMC_RESERVED_PIN_COUNT,
                "FMC_RESERVED_PIN_COUNT disagrees with the pins defined in the variant");

  // 3. One spot-check of an actual mapping. PE7 is the example the design note
  //    uses, and the one a user is most likely to reach for (it looks like an
  //    ordinary GPIO).
  static_assert(FMC_RESERVED_D4 == PE7, "FMC_RESERVED_D4 should be PE7");

  // 4. Driving an FMC pin still compiles -- deliberately. The guard is
  //    visibility, not prevention; runtime interception was rejected (see
  //    docs/handover/Todo/M4-fmc-pin-guard.md, "已否决"). If this ever stops
  //    compiling, someone has changed the decision without changing the note.
  pinMode(PE7, OUTPUT);
  digitalWrite(PE7, HIGH);

  (void)reserved;
}

void loop() {}
