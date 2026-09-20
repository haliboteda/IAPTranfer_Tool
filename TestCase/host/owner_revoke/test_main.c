/*
 * T2-21: the root in force is never revoked by an 'R' record that names it
 * (rule R4), while the other names in that same record still are.
 *
 * Runs the REAL owner_root_ro.c from the Arduino core over a RAM buffer that
 * holds hand-built records in the on-flash byte layout of owner_slot.h. The
 * records are laid out by byte offset rather than through owner_record_t, so
 * a layout change in either file shows up here as a failed assertion instead
 * of being silently followed.
 *
 * Build & run: python build.py
 */

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include "owner_root_ro.h"
#include "fw_pubkey.h"
#include "iap_keyderive.h"
#include "fake_owner_area.h"
#include "iap_keyderive_stub.h"

/* The record area owner_root_ro.c reads, in place of memory-mapped flash.
 * Aligned because owner_record_t has 32-bit fields. */
_Alignas(8) uint8_t fake_owner_area[FAKE_OWNER_AREA_SIZE];

/* Field offsets and sizes: open_plc_cube_ide/IAPServer/owner_slot.h. */
#define REC_SIZE        160U
#define OFF_TYPE          0U
#define OFF_RESERVED0     1U
#define OFF_FORMAT_VER    2U
#define OFF_GENERATION    4U
#define OFF_FLAGS         8U
#define OFF_PAYLOAD      12U
#define OFF_UID          76U
#define OFF_PREV_SIG     88U
#define FORMAT_VER        3U
#define REVOKE_SLOTS      4U
#define REVOKE_PREFIX    16U
#define PUBKEY_SIZE      64U

static int g_failures = 0;

#define CHECK(cond, desc) do { \
	if (cond) { printf("[PASS] %s\n", (desc)); } \
	else { printf("[FAIL] %s\n", (desc)); g_failures++; } \
} while (0)

/* Four public keys that differ inside the first REVOKE_PREFIX bytes, which is
 * all a revocation entry names. Not real P-256 points: no signature is
 * verified along this path (every record here is unsigned), and the code under
 * test only ever compares these bytes. */
static uint8_t g_root_in_force[PUBKEY_SIZE];
static uint8_t g_leaf_named_beside_root[PUBKEY_SIZE];
static uint8_t g_leaf_named_alone[PUBKEY_SIZE];
static uint8_t g_leaf_never_named[PUBKEY_SIZE];

static void fill_key(uint8_t key[PUBKEY_SIZE], uint8_t first)
{
	uint32_t i;

	for (i = 0U; i < PUBKEY_SIZE; i++) {
		key[i] = (uint8_t)(first + i);
	}
}

static uint8_t *record(uint32_t index)
{
	return &fake_owner_area[index * REC_SIZE];
}

static void put_u16(uint8_t *p, uint16_t v)
{
	p[0] = (uint8_t)(v & 0xFFU);
	p[1] = (uint8_t)(v >> 8);
}

static void put_u32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)(v & 0xFFU);
	p[1] = (uint8_t)((v >> 8) & 0xFFU);
	p[2] = (uint8_t)((v >> 16) & 0xFFU);
	p[3] = (uint8_t)((v >> 24) & 0xFFU);
}

/* Common head of any record: type, format version, generation, no flags, this
 * board's uid, and an all-zero prev_sig. Unsigned is legitimate for the first
 * 'O' record (trust on first use) and is what 'R' records get here -- their
 * signature is checked when they are appended, not when they are read. */
static uint8_t *begin_record(uint32_t index, uint8_t type, uint32_t generation)
{
	uint8_t *r = record(index);

	memset(r, 0, REC_SIZE);
	r[OFF_TYPE] = type;
	r[OFF_RESERVED0] = 0U;
	put_u16(&r[OFF_FORMAT_VER], (uint16_t)FORMAT_VER);
	put_u32(&r[OFF_GENERATION], generation);
	put_u32(&r[OFF_FLAGS], 0UL);
	memcpy(&r[OFF_UID], test_machine_id(), IAP_MACHINE_ID_SIZE);
	return r;
}

static void write_owner_record(uint32_t index, uint32_t generation,
		const uint8_t root_pubkey[PUBKEY_SIZE])
{
	uint8_t *r = begin_record(index, (uint8_t)'O', generation);

	memcpy(&r[OFF_PAYLOAD], root_pubkey, PUBKEY_SIZE);
}

/* An 'R' record names up to four leaves by the first 16 bytes of their public
 * key. Unused slots stay zero and are skipped by the code under test. */
static void write_revoke_record(uint32_t index, uint32_t generation,
		const uint8_t *named[], uint32_t count)
{
	uint8_t *r = begin_record(index, (uint8_t)'R', generation);
	uint32_t i;

	for (i = 0U; (i < count) && (i < REVOKE_SLOTS); i++) {
		memcpy(&r[OFF_PAYLOAD + (i * REVOKE_PREFIX)], named[i], REVOKE_PREFIX);
	}
}

/* Erased flash reads as 0xFF, so an untouched area must look erased -- a
 * zero-filled one would be a record shape that never occurs on a real board. */
static void arrange_board(void)
{
	const uint8_t *named_with_root[2];
	const uint8_t *named_alone[1];

	memset(fake_owner_area, 0xFF, sizeof(fake_owner_area));

	fill_key(g_root_in_force, 0xA0U);
	fill_key(g_leaf_named_beside_root, 0xB0U);
	fill_key(g_leaf_named_alone, 0xC0U);
	fill_key(g_leaf_never_named, 0xD0U);

	write_owner_record(0U, 1UL, g_root_in_force);

	named_with_root[0] = g_root_in_force;
	named_with_root[1] = g_leaf_named_beside_root;
	write_revoke_record(1U, 2UL, named_with_root, 2U);

	named_alone[0] = g_leaf_named_alone;
	write_revoke_record(2U, 3UL, named_alone, 1U);
}

int main(void)
{
	uint8_t resolved_root[PUBKEY_SIZE];

	arrange_board();

	/* Without this the rest would only prove R4 against the compiled-in
	 * fallback key, which no record can name by accident anyway. */
	owner_root_ro_get(resolved_root);
	CHECK(memcmp(resolved_root, g_root_in_force, PUBKEY_SIZE) == 0,
			"the chain resolves to the root the 'O' record installed");
	CHECK(memcmp(resolved_root, fw_public_key, PUBKEY_SIZE) != 0,
			"that root is not the compiled-in fallback");

	/* R4. */
	CHECK(!owner_root_ro_is_revoked(g_root_in_force),
			"an 'R' entry naming the root in force is ignored");

	/* R4 skips the one entry, not the record it sits in. */
	CHECK(owner_root_ro_is_revoked(g_leaf_named_beside_root),
			"the other leaf named by that same record is still revoked");

	/* Positive control: without it, "is_revoked says no" could mean the area
	 * was never read at all. */
	CHECK(owner_root_ro_is_revoked(g_leaf_named_alone),
			"a leaf named by a plain 'R' record is revoked");

	CHECK(!owner_root_ro_is_revoked(g_leaf_never_named),
			"a leaf no record names is not revoked");

	printf("\n%s (%d failure%s)\n", (g_failures == 0) ? "ALL PASS" : "FAILED",
			g_failures, (g_failures == 1) ? "" : "s");
	return (g_failures == 0) ? 0 : 1;
}
