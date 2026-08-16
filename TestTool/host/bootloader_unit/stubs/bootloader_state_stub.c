/*
 * iap_auth.c calls exactly one function from the real bootloader_state.c:
 * the crypto self-test gate. bootloader_state.h itself is clean (no HAL
 * dependency) and is compiled as-is; the rest of bootloader_state.c (Flash
 * journal read/write) is out of scope for this harness, so it's stubbed
 * here instead of pulling in the real Flash/HAL code.
 */

#include "bootloader_state.h"

bool bootloader_state_crypto_selftest_passed(void)
{
	return true;
}
