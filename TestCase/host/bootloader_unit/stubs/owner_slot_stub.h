/*
 * Test-only control surface for owner_slot_stub.c: which root the fake board
 * currently trusts.
 */

#ifndef HOSTTEST_OWNER_SLOT_STUB_H_
#define HOSTTEST_OWNER_SLOT_STUB_H_

#include <stdint.h>

void test_owner_set_root(const uint8_t root[64]);

#endif /* HOSTTEST_OWNER_SLOT_STUB_H_ */
