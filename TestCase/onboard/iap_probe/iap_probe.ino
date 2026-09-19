// Minimal app image for boot + IAP acceptance runs.
// Prints a banner the test scripts can anchor on, then idles.
//
// Build (normal):
//   arduino-cli compile --fqbn OpenPLC_Alpha:stm32:OPEN-PLC \
//       --output-dir <dir> TestCase/onboard/iap_probe
//
// Build with a MAC that is NOT the one derived from the chip UID -- this is what
// R1-13 needs, and it takes no code change: OpenPLC_Net/src/ethernetif.c uses
// MAC_ADDR0..5 verbatim when all six are defined, and only derives from the UID
// otherwise.
//
//   FLAGS="-DVECT_TAB_OFFSET={build.flash_offset} \
//          -DMAC_ADDR0=0x02 -DMAC_ADDR1=0xBB -DMAC_ADDR2=0x49 \
//          -DMAC_ADDR3=0xDE -DMAC_ADDR4=0xAD -DMAC_ADDR5=0x01"
//   arduino-cli compile --fqbn OpenPLC_Alpha:stm32:OPEN-PLC --clean \
//       --build-property "compiler.c.extra_flags=$FLAGS" \
//       --build-property "compiler.cpp.extra_flags=$FLAGS" \
//       --output-dir <dir> TestCase/onboard/iap_probe
//
// ⛔ -DVECT_TAB_OFFSET={build.flash_offset} is not optional. --build-property
// REPLACES the property, and platform.txt already sets compiler.c.extra_flags to
// exactly that define. Dropping it builds an image whose vector table is at the
// wrong offset: it flashes and verifies fine, then faults the instant the
// bootloader jumps into it, leaving a board that answers neither ethernet nor
// CDC. Recovering one takes the BOOT0 gesture. Measured 2026-09-18.
//
// The board takes its address by DHCP, so a changed MAC also changes the IP.
// That is the point of R1-13: find the board by broadcast and UID, not by MAC.

void setup() {
  Serial.begin(115200);
  Serial.println("IAP_PROBE_APP up");
}

void loop() {
  Serial.println("IAP_PROBE_APP alive");
  delay(1000);
}
