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

/* All the registers hal_stub.c's s_bkp[8] can back, not just the ones in use
 * today. The bootloader moved its VBAT witness from DR2 to DR3 on 2026-08-17
 * (see IAPServer/iap_auth.c) and this header still stopped at DR2, so the
 * harness would not compile -- and nobody noticed, because it had been
 * reporting SKIP for want of a host compiler. Defining the whole range the
 * stub supports keeps the next reallocation from breaking the build.
 *
 * Backup register allocation is shared across three repositories; the record
 * is the table in open_plc_cube_ide/docs/ARCHITECTURE.md. */
#define RTC_BKP_DR0 0U
#define RTC_BKP_DR1 1U
#define RTC_BKP_DR2 2U
#define RTC_BKP_DR3 3U
#define RTC_BKP_DR4 4U
#define RTC_BKP_DR5 5U
#define RTC_BKP_DR6 6U
#define RTC_BKP_DR7 7U

uint32_t HAL_RTCEx_BKUPRead(RTC_HandleTypeDef *hrtc_handle, uint32_t backup_register);
void HAL_RTCEx_BKUPWrite(RTC_HandleTypeDef *hrtc_handle, uint32_t backup_register, uint32_t value);

#endif /* HOSTTEST_STUB_RTC_H_ */
