/*
 * iap_auth.c and fw_verify.c ask owner_slot.c two questions: which root does
 * this board trust, and has this leaf been revoked. The rest of owner_slot.c
 * is a flash journal, out of scope for this harness, so only those two
 * answers are provided here -- both settable, so a test can put the board on
 * a root of its choosing and revoke a specific leaf.
 *
 * 2026-09-20: added owner_slot_is_revoked() alongside the real iap_cert.c /
 * iap_auth.c gaining a revocation parameter. Defaults to "nothing revoked" so
 * every test that predates revocation keeps passing unchanged.
 */

#include "owner_slot.h"
#include "owner_slot_stub.h"
#include <string.h>

static uint8_t s_root[64];
static uint8_t s_revoked[64];
static bool s_have_revoked;

void test_owner_set_root(const uint8_t root[64])
{
	memcpy(s_root, root, sizeof(s_root));
}

void test_owner_revoke(const uint8_t leaf_pubkey[64])
{
	memcpy(s_revoked, leaf_pubkey, sizeof(s_revoked));
	s_have_revoked = true;
}

void test_owner_clear_revocations(void)
{
	s_have_revoked = false;
}

const uint8_t *owner_slot_root(void)
{
	return s_root;
}

bool owner_slot_is_revoked(const uint8_t leaf_pubkey[64])
{
	/* Same 16-byte prefix comparison as the real owner_slot_is_revoked() --
	 * see open_plc_cube_ide/IAPServer/owner_slot.c. */
	return s_have_revoked && (memcmp(leaf_pubkey, s_revoked, 16U) == 0);
}
