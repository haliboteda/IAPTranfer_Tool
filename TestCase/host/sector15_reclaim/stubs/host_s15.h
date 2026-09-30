/*
 * Maps sector 15 and backup SRAM onto RAM buffers. Passed with -include, so
 * it is seen before the (guarded) firmware defaults in owner_slot.h and
 * bootloader_state.c.
 *
 * T2-34 -- see $PROD/docs/modules/M2-ownership.md.
 */

#ifndef HOSTTEST_HOST_S15_H_
#define HOSTTEST_HOST_S15_H_

#include <stdint.h>

#define BOOTLOADER_STATE_HOST_TEST 1

#define FAKE_SECTOR_SIZE  (128U * 1024U)
#define FAKE_BKPSRAM_SIZE 4096U

extern uint8_t fake_sector[FAKE_SECTOR_SIZE];
extern uint8_t fake_bkpsram[FAKE_BKPSRAM_SIZE];

/* Calibration takes the first 8 KiB, as on the board. */
#define OWNER_SLOT_BASE ((uintptr_t)fake_sector + (8U * 1024U))

#endif /* HOSTTEST_HOST_S15_H_ */
