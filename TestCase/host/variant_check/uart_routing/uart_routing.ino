/*
 * P4 acceptance: the two serial channels of the variant stay where they are.
 *
 * Compile-only. Nothing here is meant to run on a board -- the whole check is
 * in the static_asserts, which fail the build if a channel has been moved.
 *
 * The two channels, and why they must not swap:
 *
 *   printf / stdout   USART3 AF7, PC10/PC11 -> MAX3221 -> RS232 terminals C05/C06
 *   Serial1           UART4  AF8, PH13/PH14 -> JunctionLink expansion port J4-11/J4-12
 *
 * A U(S)ART peripheral has one handle slot in the core, so putting printf on
 * UART4 would make it fight the expansion port. Routing table and the measured
 * evidence: $PROD/docs/hardware/HARDWARE-FACTS.md, "UART4 与 USART3".
 *
 * What it does NOT check: that printf actually reaches the RS232 terminal.
 * That needs a board (PB10 must be driven high first) and belongs to an
 * on-board case, not here.
 */

/* DEBUG_UART expands to a pointer cast, which C++ will not accept inside a
 * constant expression. Compare the two macros' expanded text instead: identical
 * text means DEBUG_UART is the same peripheral macro as USART3. */
#define UART_ROUTING_STR_(x)  #x
#define UART_ROUTING_STR(x)   UART_ROUTING_STR_(x)

constexpr bool same_text(const char *a, const char *b) {
  return (*a == *b) && ((*a == '\0') || same_text(a + 1, b + 1));
}

void setup() {
  /* ---- Channel 1: the printf console must stay on the RS232 terminals ---- */

  static_assert(same_text(UART_ROUTING_STR(DEBUG_UART), UART_ROUTING_STR(USART3)),
                "printf console moved off USART3. The RS232 terminals C05/C06 "
                "are PC10/PC11, and on this board only USART3 (AF7) may drive "
                "them in an app -- UART4 belongs to the expansion port. "
                "Fix variant_PLC_H743.h, do not change this assert.");

  static_assert(DEBUG_PINNAME_TX == PC_10_ALT1,
                "printf console TX is no longer pinned to PC_10_ALT1. Leaving "
                "it unpinned makes the core pick the first USART3 TX row in "
                "PeripheralPins.c, which on this board is PB10 = RS232_EN -- "
                "printf would reconfigure the transceiver enable pin.");

  /* ---- Channel 2: the expansion port keeps PH13/PH14 (UART4) ------------- */

  static_assert(PIN_SERIAL_TX == PH13,
                "PIN_SERIAL_TX moved off PH13. PH13/PH14 are the JunctionLink "
                "expansion port J4-11/J4-12 and are reserved for it; the RS232 "
                "console is a separate channel on PC10/PC11.");

  static_assert(PIN_SERIAL_RX == PH14,
                "PIN_SERIAL_RX moved off PH14. PH13/PH14 are the JunctionLink "
                "expansion port J4-11/J4-12 and are reserved for it; the RS232 "
                "console is a separate channel on PC10/PC11.");

  static_assert(SERIAL_UART_INSTANCE == 4,
                "SERIAL_UART_INSTANCE no longer names UART4, so it disagrees "
                "with PIN_SERIAL_TX/RX (PH13/PH14, which are UART4 AF8).");

  /* ---- The two channels must not land on the same pins ------------------- */

  static_assert(PIN_SERIAL_TX != PC10 && PIN_SERIAL_RX != PC11,
                "The expansion port has been pointed at the RS232 pins "
                "PC10/PC11. The two channels would then share one cable pair; "
                "they are meant to be usable at the same time.");
}

void loop() {}
