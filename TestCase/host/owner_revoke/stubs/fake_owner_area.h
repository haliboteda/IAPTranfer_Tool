/*
 * Redirects owner_root_ro.c's record area from memory-mapped flash to a RAM
 * buffer this harness fills. Passed to the compiler with -include, so it is
 * seen before owner_root_ro.c's own (guarded) default.
 *
 * T2-21 -- see $PROD/docs/modules/M2-ownership.md.
 */

#ifndef HOSTTEST_FAKE_OWNER_AREA_H_
#define HOSTTEST_FAKE_OWNER_AREA_H_

#include <stdint.h>

/* 8 KiB, the size owner_slot.h reserves for the area. */
#define FAKE_OWNER_AREA_SIZE (8U * 1024U)

extern uint8_t fake_owner_area[FAKE_OWNER_AREA_SIZE];

#define OWNER_SLOT_BASE ((uintptr_t)fake_owner_area)

#endif /* HOSTTEST_FAKE_OWNER_AREA_H_ */
