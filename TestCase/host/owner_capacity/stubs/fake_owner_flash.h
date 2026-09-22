/*
 * Redirects owner_slot.c's record area from memory-mapped flash to a RAM
 * buffer, and gives it a Flash_If_Write() that behaves like the real one.
 * Passed to the compiler with -include, so it is seen before owner_slot.h's
 * own (guarded) default.
 *
 * T2-22 / T2-23 -- see $PROD/docs/modules/M2-ownership.md.
 */

#ifndef HOSTTEST_FAKE_OWNER_FLASH_H_
#define HOSTTEST_FAKE_OWNER_FLASH_H_

#include <stdint.h>

/* 8 KiB, the size owner_slot.h reserves for the area. */
#define FAKE_OWNER_AREA_SIZE (8U * 1024U)

extern uint8_t fake_owner_area[FAKE_OWNER_AREA_SIZE];

#define OWNER_SLOT_BASE ((uintptr_t)fake_owner_area)

/* Bytes Flash_If_Write() has actually programmed since the last reset of the
 * counter. "Not one byte was written" is a criterion for T2-23, and counting
 * is the only way to check it that a later refusal cannot fake. */
extern uint32_t fake_flash_bytes_written;

void fake_flash_reset(void);

#endif /* HOSTTEST_FAKE_OWNER_FLASH_H_ */
