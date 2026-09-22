/*
 * A Flash_If_Write() that programs a RAM buffer under the same rules the H7
 * enforces, so a bug this harness cannot see is one the board would not have
 * either:
 *
 *   - 32-byte granularity, aligned      the H7 programs one 256-bit word
 *   - program once                      a byte that is not 0xFF cannot be
 *                                       written again without a sector erase
 *   - inside the area                   anything else would be the bootloader
 *                                       overwriting its own code
 *
 * Any of those refuses the whole call and writes nothing, which is also what
 * the real Flash_If_Write() reports as a failure.
 */

#include "fake_owner_flash.h"
#include <string.h>

_Alignas(8) uint8_t fake_owner_area[FAKE_OWNER_AREA_SIZE];
uint32_t fake_flash_bytes_written;

void fake_flash_reset(void)
{
	memset(fake_owner_area, 0xFF, sizeof(fake_owner_area));
	fake_flash_bytes_written = 0U;
}

uint16_t Flash_If_Write(uint8_t *DataAddress, uint8_t *FlashAddress, uint32_t Len)
{
	uintptr_t base = (uintptr_t)fake_owner_area;
	uintptr_t dest = (uintptr_t)FlashAddress;
	uint32_t offset;
	uint32_t i;

	if ((dest < base) || (dest + Len > base + FAKE_OWNER_AREA_SIZE)) {
		return 1U;
	}
	offset = (uint32_t)(dest - base);
	if (((offset % 32U) != 0U) || ((Len % 32U) != 0U) || (Len == 0U)) {
		return 2U;
	}
	for (i = 0U; i < Len; i++) {
		if (fake_owner_area[offset + i] != 0xFFU) {
			return 3U;   /* program-once violation */
		}
	}
	memcpy(&fake_owner_area[offset], DataAddress, Len);
	fake_flash_bytes_written += Len;
	return 0U;
}
