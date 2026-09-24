/*
 * Stand-in for the real Core/Inc/rng.h. Only what iap_auth.c needs: the `hrng`
 * handle it passes through, and the two RNG calls it makes.
 *
 * What these hand back is set per test through hal_stub.h. The nonce has to be
 * reproducible or a golden signature over it cannot mean anything.
 */

#ifndef HOSTTEST_STUB_RNG_H_
#define HOSTTEST_STUB_RNG_H_

#include <stdint.h>

typedef struct { int unused; } RNG_HandleTypeDef;
extern RNG_HandleTypeDef hrng;

typedef enum {
	HAL_OK    = 0,
	HAL_ERROR = 1
} HAL_StatusTypeDef;

HAL_StatusTypeDef HAL_RNG_GenerateRandomNumber(RNG_HandleTypeDef *hrng_handle,
		uint32_t *random32bit);
uint32_t HAL_RNG_GetError(RNG_HandleTypeDef *hrng_handle);

#endif /* HOSTTEST_STUB_RNG_H_ */
