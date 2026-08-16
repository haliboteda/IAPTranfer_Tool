/*
 * Test-only control surface for the fake HAL in hal_stub.c. A test calls
 * these to set up the "hardware state" (which device it's pretending to be,
 * what time it is) before exercising the real iap_auth.c / iap_keyderive.c.
 */

#ifndef HOSTTEST_HAL_STUB_H_
#define HOSTTEST_HAL_STUB_H_

#include <stdint.h>

/* Zeroes the fake tick, UID, and every RTC backup register. */
void test_hal_reset(void);

/* Sets what HAL_GetUIDw0/1/2() return, i.e. which device this "is". */
void test_hal_set_uid(uint32_t uid0, uint32_t uid1, uint32_t uid2);

/* Sets what HAL_GetTick() returns. */
void test_hal_set_tick(uint32_t tick_ms);

/* Directly sets an RTC backup register, e.g. RTC_BKP_DR1, to control what
 * iap_auth's next_counter() reads as the previously-stored counter value. */
void test_hal_set_bkp(uint32_t reg, uint32_t value);

#endif /* HOSTTEST_HAL_STUB_H_ */
