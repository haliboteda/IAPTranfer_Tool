#include "main.h"
#include "rtc.h"
#include "hal_stub.h"

RTC_HandleTypeDef hrtc;

static uint32_t s_tick;
static uint32_t s_uid0, s_uid1, s_uid2;
static uint32_t s_bkp[8]; /* only indices 1 (DR1) and 2 (DR2) are used */

void test_hal_reset(void)
{
	uint32_t i;

	s_tick = 0U;
	s_uid0 = s_uid1 = s_uid2 = 0U;
	for (i = 0U; i < 8U; i++) {
		s_bkp[i] = 0U;
	}
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
