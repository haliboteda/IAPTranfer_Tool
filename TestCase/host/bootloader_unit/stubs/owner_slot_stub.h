/*
 * Test-only control surface for owner_slot_stub.c: which root the fake board
 * currently trusts, and which leaf (if any) it has revoked.
 */

#ifndef HOSTTEST_OWNER_SLOT_STUB_H_
#define HOSTTEST_OWNER_SLOT_STUB_H_

#include <stdint.h>

void test_owner_set_root(const uint8_t root[64]);

/* Marks leaf_pubkey as revoked. Only one at a time -- no test here needs more. */
void test_owner_revoke(const uint8_t leaf_pubkey[64]);

/* Back to "nothing revoked", the default. */
void test_owner_clear_revocations(void);

#endif /* HOSTTEST_OWNER_SLOT_STUB_H_ */
