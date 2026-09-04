/*
 * iap_auth.c and fw_verify.c ask owner_slot.c one question: which root does
 * this board trust. The rest of owner_slot.c is a flash journal, out of scope
 * for this harness, so only that answer is provided here -- settable, so a
 * test can put the board on a root of its choosing.
 */

#include "owner_slot.h"
#include "owner_slot_stub.h"
#include <string.h>

static uint8_t s_root[64];

void test_owner_set_root(const uint8_t root[64])
{
	memcpy(s_root, root, sizeof(s_root));
}

const uint8_t *owner_slot_root(void)
{
	return s_root;
}
