/*
 * Sector 15 as a RAM buffer, under the H7's rules (32-byte words, program
 * once, erase takes the whole sector), plus a power cut: when the operation
 * numbered fake_cut_at is about to run, control jumps back to the harness and
 * nothing further is written -- what a real cut leaves behind.
 */

#include "host_s15.h"
#include "stm32h7xx_hal.h"
#include <setjmp.h>
#include <string.h>

_Alignas(32) uint8_t fake_sector[FAKE_SECTOR_SIZE];
_Alignas(32) uint8_t fake_bkpsram[FAKE_BKPSRAM_SIZE];

jmp_buf  fake_cut_jmp;
int      fake_cut_at = -1;
int      fake_ops;
int      fake_erases;

static void flash_op(void)
{
	if ((fake_cut_at >= 0) && (fake_ops == fake_cut_at)) {
		longjmp(fake_cut_jmp, 1);
	}
	fake_ops++;
}

uint16_t Flash_If_Erase(uint32_t Add, uint32_t NbSectors)
{
	(void)Add;
	(void)NbSectors;
	flash_op();
	memset(fake_sector, 0xFF, sizeof(fake_sector));
	fake_erases++;
	return 0U;
}

uint16_t Flash_If_Write(uint8_t *DataAddress, uint8_t *FlashAddress, uint32_t Len)
{
	uintptr_t base = (uintptr_t)fake_sector;
	uintptr_t dest = (uintptr_t)FlashAddress;
	uint32_t offset;
	uint32_t i;

	if ((dest < base) || (dest + Len > base + FAKE_SECTOR_SIZE)) {
		return 1U;
	}
	offset = (uint32_t)(dest - base);
	if (((offset % 32U) != 0U) || ((Len % 32U) != 0U) || (Len == 0U)) {
		return 2U;
	}
	for (i = 0U; i < Len; i++) {
		if (fake_sector[offset + i] != 0xFFU) {
			return 3U;   /* program-once violation */
		}
	}
	flash_op();
	memcpy(&fake_sector[offset], DataAddress, Len);
	return 0U;
}

void HAL_PWR_EnableBkUpAccess(void)
{
}

HAL_StatusTypeDef HAL_PWREx_EnableBkUpReg(void)
{
	return HAL_OK;
}
