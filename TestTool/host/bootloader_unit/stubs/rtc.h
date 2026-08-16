/*
 * Stand-in for the real Core/Inc/rtc.h. Only what iap_auth.c needs: the
 * `hrtc` handle it passes through, and the two backup-register calls it uses
 * as a persistent (survives-reset-on-real-hardware) counter store. See
 * hal_stub.c / hal_stub.h.
 */

#ifndef HOSTTEST_STUB_RTC_H_
#define HOSTTEST_STUB_RTC_H_

#include <stdint.h>

typedef struct { int unused; } RTC_HandleTypeDef;
extern RTC_HandleTypeDef hrtc;

#define RTC_BKP_DR1 1U
#define RTC_BKP_DR2 2U

uint32_t HAL_RTCEx_BKUPRead(RTC_HandleTypeDef *hrtc_handle, uint32_t backup_register);
void HAL_RTCEx_BKUPWrite(RTC_HandleTypeDef *hrtc_handle, uint32_t backup_register, uint32_t value);

#endif /* HOSTTEST_STUB_RTC_H_ */
