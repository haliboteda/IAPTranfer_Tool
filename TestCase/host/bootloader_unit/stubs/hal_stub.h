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

/* Directly sets an RTC backup register, e.g. RTC_BKP_DR3, to control what
 * iap_auth's backup-domain witness reads back. */
void test_hal_set_bkp(uint32_t reg, uint32_t value);

/* Queues the words HAL_RNG_GenerateRandomNumber() hands out, in order;
 * iap_auth_issue_challenge() takes IAP_AUTH_NONCE_SIZE/4 of them. With nothing
 * queued the stub counts up instead, so a test that does not care what the
 * nonce is still gets a fresh one each call. */
void test_hal_set_rng_words(const uint32_t *words, uint32_t n);

/* Makes every HAL_RNG_GenerateRandomNumber() fail, so a test can pin that no
 * nonce is issued at all rather than a predictable one. */
void test_hal_set_rng_fail(int fail);

#endif /* HOSTTEST_HAL_STUB_H_ */
