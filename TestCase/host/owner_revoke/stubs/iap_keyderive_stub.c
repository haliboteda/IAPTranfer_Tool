/*
 * The real iap_keyderive.c reads the STM32 UID through Arduino.h/stm32_def.h,
 * neither of which exists on a PC. Only the machine id matters here: owner
 * records are bound to one board by it, so every record this harness writes
 * must carry the same 12 bytes this returns.
 */

#include "iap_keyderive.h"
#include "iap_keyderive_stub.h"
#include <string.h>

static const uint8_t k_uid[IAP_MACHINE_ID_SIZE] = {
	0x11, 0x22, 0x33, 0x44, 0x55, 0x66,
	0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC,
};

const uint8_t *test_machine_id(void)
{
	return k_uid;
}

void iap_keyderive_get_machine_id(uint8_t out_id[IAP_MACHINE_ID_SIZE])
{
	memcpy(out_id, k_uid, sizeof(k_uid));
}
