#include "main.h"
#include "rtc.h"
#include "rng.h"
#include "hal_stub.h"

RTC_HandleTypeDef hrtc;
RNG_HandleTypeDef hrng;

#define RNG_QUEUE_LEN 8U

static uint32_t s_tick;
static uint32_t s_uid0, s_uid1, s_uid2;
static uint32_t s_bkp[8]; /* only index 3 (DR3, the VBAT witness) is used */

static uint32_t s_rng[RNG_QUEUE_LEN];
static uint32_t s_rng_len;
static uint32_t s_rng_next;
static uint32_t s_rng_seq;
static int      s_rng_fail;

void test_hal_reset(void)
{
	uint32_t i;

	s_tick = 0U;
	s_uid0 = s_uid1 = s_uid2 = 0U;
	for (i = 0U; i < 8U; i++) {
		s_bkp[i] = 0U;
	}
	s_rng_len = 0U;
	s_rng_next = 0U;
	s_rng_seq = 0U;
	s_rng_fail = 0;
}

void test_hal_set_uid(uint32_t uid0, uint32_t uid1, uint32_t uid2)
{
	s_uid0 = uid0;
	s_uid1 = uid1;
	s_uid2 = uid2;
}

void test_hal_set_tick(uint32_t tick_ms)
{
	s_tick = tick_ms;
}

void test_hal_set_bkp(uint32_t reg, uint32_t value)
{
	if (reg < 8U) {
		s_bkp[reg] = value;
	}
}

void test_hal_set_rng_words(const uint32_t *words, uint32_t n)
{
	uint32_t i;

	s_rng_len = (n > RNG_QUEUE_LEN) ? RNG_QUEUE_LEN : n;
	for (i = 0U; i < s_rng_len; i++) {
		s_rng[i] = words[i];
	}
	s_rng_next = 0U;
}

void test_hal_set_rng_fail(int fail)
{
	s_rng_fail = fail;
}

HAL_StatusTypeDef HAL_RNG_GenerateRandomNumber(RNG_HandleTypeDef *hrng_handle,
		uint32_t *random32bit)
{
	(void)hrng_handle;

	if (s_rng_fail) {
		return HAL_ERROR;
	}
	if (s_rng_next < s_rng_len) {
		*random32bit = s_rng[s_rng_next++];
	} else {
		/* Nothing queued: count up. Never repeats within a test, which is all
		 * a test that does not pin the nonce needs. */
		*random32bit = ++s_rng_seq;
	}
	return HAL_OK;
}

uint32_t HAL_RNG_GetError(RNG_HandleTypeDef *hrng_handle)
{
	(void)hrng_handle;
	return s_rng_fail ? 1U : 0U;
}

uint32_t HAL_GetTick(void) { return s_tick; }
uint32_t HAL_GetUIDw0(void) { return s_uid0; }
uint32_t HAL_GetUIDw1(void) { return s_uid1; }
uint32_t HAL_GetUIDw2(void) { return s_uid2; }
void HAL_PWR_EnableBkUpAccess(void) { }
void HAL_PWR_DisableBkUpAccess(void) { }

uint32_t HAL_RTCEx_BKUPRead(RTC_HandleTypeDef *hrtc_handle, uint32_t backup_register)
{
	(void)hrtc_handle;
	return (backup_register < 8U) ? s_bkp[backup_register] : 0U;
}

void HAL_RTCEx_BKUPWrite(RTC_HandleTypeDef *hrtc_handle, uint32_t backup_register, uint32_t value)
{
	(void)hrtc_handle;
	if (backup_register < 8U) {
		s_bkp[backup_register] = value;
	}
}
