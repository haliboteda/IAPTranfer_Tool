/* Required by the core since 2026-09-21: a sketch without a version does not
 * link. Test fixtures all use 1.0.0 -- the upload gate lets equal versions
 * through, so this never blocks re-flashing a fixture. */
OPENPLC_APP_VERSION(1, 0, 0);

// Measures how many low bits SCB->VTOR actually implements on this silicon,
// and reports where the bootloader left it.
//
// Evidence for the ticket "VTOR 对齐到底要多少字节"
// ($PROD/maps/app-header-replaces-journal/issues/HDR-04-what-is-the-real-vtor-alignment.md):
// the header in front of the app has to be a whole alignment unit, and the
// number driving that (1024) rests partly on core_cm7.h -- a copy, not the
// chip. This asks the chip.
//
// What it does NOT settle: the architectural rule that the alignment must also
// be at least the vector table's own length rounded up to a power of two. That
// is a statement about UNPREDICTABLE behaviour; a part that happens to run
// proves nothing. Read it out of the ARM ARM or accept the rule.
//
// Safe to run: VTOR is written and put straight back, no flash is touched and
// no interrupt is taken in between (they are masked across the probe). The
// board keeps running afterwards.
//
// Build (one line, and the VECT_TAB_OFFSET define must survive -- --build-property
// REPLACES the flag list, it does not append to it):
//   arduino-cli compile --fqbn OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743 \
//     --build-property "compiler.c.extra_flags=-DVECT_TAB_OFFSET={build.flash_offset}" \
//     --build-property "compiler.cpp.extra_flags=-DVECT_TAB_OFFSET={build.flash_offset}" \
//     TestCase/onboard/vtor_probe
//
// Read the result on RS232 (PC10/PC11), 115200. Both channels carry it.

static void report(const char *what, uint32_t wrote, uint32_t read_back) {
  // %08lX on both channels: Serial is USB CDC, Serial_Test is the RS232
  // console. Whichever cable is plugged in, the number is there.
  Serial.printf("%-22s wrote 0x%08lX  read 0x%08lX  dropped 0x%08lX\r\n",
                what, wrote, read_back, wrote ^ read_back);
  printf("%-22s wrote 0x%08lX  read 0x%08lX  dropped 0x%08lX\r\n",
         what, wrote, read_back, wrote ^ read_back);
}

static uint32_t probe_vtor(uint32_t candidate) {
  uint32_t saved = SCB->VTOR;
  uint32_t seen;

  __disable_irq();
  SCB->VTOR = candidate;
  __DSB();
  seen = SCB->VTOR;
  SCB->VTOR = saved;
  __DSB();
  __ISB();
  __enable_irq();

  return seen;
}

void setup() {
  pinMode(RS232_EN_Pin, OUTPUT);
  digitalWrite(RS232_EN_Pin, HIGH);
  setvbuf(stdout, NULL, _IONBF, 0);

  Serial.begin(115200);
  Serial_Test.begin(115200);
  delay(2000);          // let a terminal attach before the one-shot report

  uint32_t live = SCB->VTOR;
  Serial.printf("VTOR_PROBE up\r\n");
  printf("VTOR_PROBE up\r\n");
  report("as the bootloader left", live, live);

  // Each candidate sets one low bit that 1024-byte alignment forbids. A bit
  // that comes back set is a bit the hardware implements, so the alignment the
  // hardware enforces is the lowest bit that survives.
  report("bit0  (+0x001)", live | 0x001UL, probe_vtor(live | 0x001UL));
  report("bit4  (+0x010)", live | 0x010UL, probe_vtor(live | 0x010UL));
  report("bit6  (+0x040)", live | 0x040UL, probe_vtor(live | 0x040UL));
  report("bit7  (+0x080)", live | 0x080UL, probe_vtor(live | 0x080UL));
  report("bit8  (+0x100)", live | 0x100UL, probe_vtor(live | 0x100UL));
  report("bit9  (+0x200)", live | 0x200UL, probe_vtor(live | 0x200UL));

  Serial.printf("VTOR_PROBE done, VTOR restored to 0x%08lX\r\n", SCB->VTOR);
  printf("VTOR_PROBE done, VTOR restored to 0x%08lX\r\n", SCB->VTOR);
}

void loop() {
  // Heartbeat, so "no output" can be told apart from "the board is dead".
  Serial_Test.println("VTOR_PROBE alive");
  delay(1000);
}
