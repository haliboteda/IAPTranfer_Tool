/*
 * Stand-in for the real STM32CubeMX-generated Core/Inc/main.h. Only declares
 * the handful of HAL calls iap_auth.c / iap_keyderive.c actually use, so
 * those two files (and sha256.c, which needs nothing) can be compiled and
 * run natively on the host instead of only on the STM32 target. See
 * hal_stub.c for the fake implementations and hal_stub.h for how a test
 * controls what they return.
 */

#ifndef HOSTTEST_STUB_MAIN_H_
#define HOSTTEST_STUB_MAIN_H_

#include <stdint.h>

uint32_t HAL_GetTick(void);
uint32_t HAL_GetUIDw0(void);
uint32_t HAL_GetUIDw1(void);
uint32_t HAL_GetUIDw2(void);
void HAL_PWR_EnableBkUpAccess(void);
void HAL_PWR_DisableBkUpAccess(void);

#endif /* HOSTTEST_STUB_MAIN_H_ */
