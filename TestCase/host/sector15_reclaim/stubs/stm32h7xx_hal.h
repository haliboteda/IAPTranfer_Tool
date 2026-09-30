/*
 * The handful of HAL names bkp_stash.c uses, over a RAM buffer.
 */

#ifndef HOSTTEST_STM32H7XX_HAL_H_
#define HOSTTEST_STM32H7XX_HAL_H_

#include <stdint.h>

typedef enum { HAL_OK = 0, HAL_ERROR = 1 } HAL_StatusTypeDef;

#define D3_BKPSRAM_BASE ((uintptr_t)fake_bkpsram)
#define __HAL_RCC_BKPRAM_CLK_ENABLE() do { } while (0)
#define __DSB() do { } while (0)

void HAL_PWR_EnableBkUpAccess(void);
HAL_StatusTypeDef HAL_PWREx_EnableBkUpReg(void);

#endif /* HOSTTEST_STM32H7XX_HAL_H_ */
