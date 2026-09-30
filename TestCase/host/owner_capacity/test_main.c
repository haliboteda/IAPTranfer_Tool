/*
 * T2-22: the boot line starts warning with OWNER_REVOKE_LOW_WATER revocation
 *        slots left, and says nothing before that.
 * T2-23: the 97th revocation is refused and not one byte is written.
 * T1-33: owner_slot_build_carry() keeps only the record in force (unsigned)
 *        and the live revocations, never promotes a record the chain refused,
 *        and the result still resolves to the same root when it is read back.
 * T2-24: owner_slot_build_wipe_carry() refuses a handover the board would
 *        refuse anyway, and when it accepts one the carry holds that record
 *        and nothing else.
 * T2-31: a board with no root trusts nothing and accepts a takeown with no
 *        gate; a second takeown, a setowner and a revoke are refused.
 * T2-32: a factory reset returns the board to no root, and it can be claimed
 *        again.
 * T2-33: more than 32 handovers in a row: the full 'O' segment is reclaimed,
 *        the root and the revocations survive, and nothing else is needed.
 *
 * One phase per run -- owner_slot.c caches pointers into the record area, so
 * a second arrangement needs a second process. The compact and wipe phases
 * hand their result to the next phase through COMPACTED_PATH.
 *
 * Runs the REAL owner_slot.c from the bootloader over a RAM buffer that
 * stands in for the owner-record area, with a Flash_If_Write() that enforces
 * the H7's programming rules (stubs/fake_owner_flash.c). Records are built
 * here by byte offset rather than through owner_revoke_rec_t, so a layout
 * change on either side shows up as a failed assertion instead of being
 * silently followed.
 *
 * Build & run: python build.py
 */

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <unistd.h>

#include "owner_slot.h"
#include "fw_verify.h"
#include "sha256.h"
#include "iap_keyderive.h"
#include "fake_owner_flash.h"
#include "bootloader_state.h"
#include "iap_keyderive_stub.h"
#include "uECC.h"

/* Field offsets: open_plc_cube_ide/IAPServer/owner_slot.h. */
#define OFF_TYPE          0U
#define OFF_RESERVED0     1U
#define OFF_FORMAT_VER    2U
#define OFF_GENERATION    4U
#define OFF_FLAGS         8U
#define OFF_PAYLOAD      12U
#define OFF_UID          76U

#define OFF_REV_UID       4U
#define OFF_REV_PREFIX   16U

#define FORMAT_VER        4U
#define PUBKEY_SIZE      64U

static int g_failures = 0;

#define CHECK(cond, desc) do { \
	if (cond) { printf("[PASS] %s\n", (desc)); } \
	else { printf("[FAIL] %s\n", (desc)); g_failures++; } \
} while (0)

static uint8_t g_root_pub[PUBKEY_SIZE];
static uint8_t g_root_priv[32];
static uint8_t g_next_pub[PUBKEY_SIZE];
static uint8_t g_next_priv[32];

/* Deterministic, so a failure reproduces. Not secret and not meant to be:
 * the keys never leave this process and nothing here is a security boundary. */
static int test_rng(uint8_t *dest, unsigned size)
{
	static uint32_t state = 0x13579BDFUL;
	unsigned i;

	for (i = 0U; i < size; i++) {
		state = (state * 1103515245UL) + 12345UL;
		dest[i] = (uint8_t)(state >> 16);
	}
	return 1;
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

extern uint32_t fake_reclaim_count;   /* stubs/reclaim_stub.c */

/* Records written straight into the buffer, for arrangements the public API
 * would never produce. */
static uint8_t *owner_slot_at(uint32_t index)
{
	return &fake_owner_area[index * OWNER_RECORD_SIZE];   /* 'O' segment is at offset 0 */
}

/* An 'O' record carrying `root`, signed by `signer_priv` or unsigned when
 * that is NULL. Unsigned is legitimate for the first record only. */
static void install_owner_record(uint32_t index, uint32_t generation,
		const uint8_t root[PUBKEY_SIZE], const uint8_t *signer_priv)
{
	uint8_t *r = owner_slot_at(index);
	uint8_t digest[SHA256_DIGEST_SIZE];

	memset(r, 0, OWNER_RECORD_SIZE);
	r[OFF_TYPE] = (uint8_t)'O';
	r[OFF_RESERVED0] = 0U;
	put_u16(&r[OFF_FORMAT_VER], (uint16_t)FORMAT_VER);
	put_u32(&r[OFF_GENERATION], generation);
	put_u32(&r[OFF_FLAGS], 0UL);
	memcpy(&r[OFF_PAYLOAD], root, PUBKEY_SIZE);
	memcpy(&r[OFF_UID], test_machine_id(), IAP_MACHINE_ID_SIZE);

	if (signer_priv != NULL) {
		sha256(r, OWNER_SIGNED_PREFIX_LEN, digest);
		if (uECC_sign(signer_priv, digest, sizeof(digest),
				&r[OWNER_SIGNED_PREFIX_LEN], uECC_secp256r1()) != 1) {
			printf("[FAIL] could not sign an owner record -- harness problem\n");
			g_failures++;
		}
	}
}

static void install_root(void)
{
	install_owner_record(0U, 1UL, g_root_pub, NULL);
}

/* 97 leaf names that differ inside the first OWNER_REVOKE_PREFIX_LEN bytes,
 * which is all a revocation names. A collision with the root's own prefix
 * would trip R4 and make a revoked leaf read back as not revoked, so the
 * first assertion in main() checks the root is the generated key and the
 * read-back loop would catch it if one ever did collide. */
static void leaf_name(uint32_t n, uint8_t out[OWNER_REVOKE_PREFIX_LEN])
{
	uint32_t i;

	for (i = 0U; i < OWNER_REVOKE_PREFIX_LEN; i++) {
		out[i] = (uint8_t)(0x10U + n + i);
	}
}

/* Sign the 32 bytes the board is about to write. Building them here, by
 * offset, is what makes this a check of the format and not just of the code
 * that happens to produce it. */
static void sign_revocation(const uint8_t leaf[OWNER_REVOKE_PREFIX_LEN],
		uint8_t sig[64])
{
	uint8_t rec[OWNER_REVOKE_REC_SIZE];
	uint8_t digest[SHA256_DIGEST_SIZE];

	memset(rec, 0, sizeof(rec));
	rec[OFF_TYPE] = (uint8_t)'R';
	rec[OFF_RESERVED0] = 0U;
	put_u16(&rec[OFF_FORMAT_VER], (uint16_t)FORMAT_VER);
	memcpy(&rec[OFF_REV_UID], test_machine_id(), IAP_MACHINE_ID_SIZE);
	memcpy(&rec[OFF_REV_PREFIX], leaf, OWNER_REVOKE_PREFIX_LEN);

	sha256(rec, sizeof(rec), digest);
	if (uECC_sign(g_root_priv, digest, sizeof(digest), sig,
			uECC_secp256r1()) != 1) {
		printf("[FAIL] could not sign a revocation -- harness problem\n");
		g_failures++;
	}
}

/* A leaf name padded out to the 64-byte public key owner_slot_is_revoked()
 * takes -- only the first OWNER_REVOKE_PREFIX_LEN bytes are ever compared. */
static bool nth_reads_back_revoked(uint32_t n)
{
	uint8_t probe[64];

	memset(probe, 0, sizeof(probe));
	leaf_name(n, probe);
	return owner_slot_is_revoked(probe);
}

static bool all_read_back_revoked(uint32_t count)
{
	uint32_t i;

	for (i = 0U; i < count; i++) {
		if (!nth_reads_back_revoked(i)) {
			return false;
		}
	}
	return true;
}

static bool revoke_nth(uint32_t n)
{
	uint8_t leaf[OWNER_REVOKE_PREFIX_LEN];
	uint8_t sig[64];
	bool already = false;

	leaf_name(n, leaf);
	sign_revocation(leaf, sig);
	return owner_slot_revoke(leaf, sig, &already);
}

/*
 * owner_slot_report() writes to stdout, and the criterion for T2-22 is what it
 * writes -- not an internal count, which would only re-state the condition
 * being tested. So stdout is redirected to a file for the length of one call.
 * dup/dup2 rather than freopen: restoring stdout afterwards has to work, and
 * freopen has no portable way back.
 */
/* The compacted image, handed from the compact phase to the phase that reads
 * it back. A file rather than memory because the two run in separate
 * processes. */
#define COMPACTED_PATH "compacted.bin"

static bool write_file(const char *path, const uint8_t *data, uint32_t len)
{
	FILE *f = fopen(path, "wb");
	bool ok;

	if (f == NULL) {
		return false;
	}
	ok = (fwrite(data, 1U, len, f) == len);
	fclose(f);
	return ok;
}

static bool read_file(const char *path, uint8_t *data, uint32_t len)
{
	FILE *f = fopen(path, "rb");
	bool ok;

	if (f == NULL) {
		return false;
	}
	ok = (fread(data, 1U, len, f) == len);
	fclose(f);
	return ok;
}

static bool report_contains(const char *needle)
{
	static char buf[8192];
	const char *path = "report_capture.tmp";
	FILE *f;
	int saved;
	size_t n;

	fflush(stdout);
	saved = dup(1);
	f = fopen(path, "w+");
	if ((saved < 0) || (f == NULL)) {
		printf("[FAIL] could not capture stdout -- harness problem\n");
		g_failures++;
		return false;
	}
	dup2(fileno(f), 1);
	owner_slot_report();
	fflush(stdout);
	dup2(saved, 1);
	close(saved);

	rewind(f);
	n = fread(buf, 1U, sizeof(buf) - 1U, f);
	buf[n] = '\0';
	fclose(f);
	remove(path);

	return strstr(buf, needle) != NULL;
}

/* owner_slot.c caches its scan. Nothing in the public API resets it, and the
 * writers do it themselves, so the only place this harness needs it is after
 * writing the first record behind owner_slot.c's back. */
static void force_rescan(void)
{
	uint8_t probe[64];

	memset(probe, 0, sizeof(probe));
	/* Any writer re-scans; none of them can be called yet. Reaching the
	 * scan through a reader means relying on owner_slot_init() having not run
	 * -- true here because install_root() is the first thing that happens. */
	(void)owner_slot_is_revoked(probe);
}


static bool all_bytes_are(const uint8_t *p, uint32_t len, uint8_t v)
{
	uint32_t i;

	for (i = 0U; i < len; i++) {
		if (p[i] != v) {
			return false;
		}
	}
	return true;
}

/* An 'R' record placed straight into the segment, for arranging an area that
 * owner_slot_revoke() would never produce (a self-naming revocation). */
static void write_revoke_slot(uint32_t index, const uint8_t leaf[OWNER_REVOKE_PREFIX_LEN])
{
	uint8_t *r = &fake_owner_area[OWNER_SEG_O_SIZE + (index * OWNER_REVOKE_REC_SIZE)];

	memset(r, 0, OWNER_REVOKE_REC_SIZE);
	r[OFF_TYPE] = (uint8_t)'R';
	r[OFF_RESERVED0] = 0U;
	put_u16(&r[OFF_FORMAT_VER], (uint16_t)FORMAT_VER);
	memcpy(&r[OFF_REV_UID], test_machine_id(), IAP_MACHINE_ID_SIZE);
	memcpy(&r[OFF_REV_PREFIX], leaf, OWNER_REVOKE_PREFIX_LEN);
}

/* R4: a revocation naming the root in force is ignored, so no sequence of
 * revocations can leave a board with no usable root.
 *
 * This is the bootloader's own copy of the rule -- the core mirror has its
 * own, covered by T2-21. owner_slot_revoke() refuses to write such a record,
 * so the only way to reach the check is to place one behind its back. */
static void phase_self_revoke(void)
{
	/* The whole area has to be arranged before the first read: owner_slot.c
	 * scans once and caches, and nothing in its public API resets that.
	 * Writing a record after the scan and expecting the reader to see it is
	 * how the first version of this phase passed without testing anything. */
	install_root();
	write_revoke_slot(0U, g_root_pub);   /* names the root in force */
	write_revoke_slot(1U, g_next_pub);   /* names something else */
	force_rescan();

	CHECK(!owner_slot_is_revoked(g_root_pub),
			"a revocation naming the root in force is ignored");
	CHECK(memcmp(owner_slot_root(), g_root_pub, PUBKEY_SIZE) == 0,
			"and the root is still the one in force");

	/* The control: without it, a reader that answers "not revoked" to
	 * everything would pass the two checks above. */
	CHECK(owner_slot_is_revoked(g_next_pub),
			"while a revocation naming any other key still bites");
}

static void phase_capacity(void)
{
	uint32_t i;
	uint8_t before[OWNER_SEG_R_SIZE];
	uint32_t bytes_before;

	install_root();
	force_rescan();

	CHECK(memcmp(owner_slot_root(), g_root_pub, PUBKEY_SIZE) == 0,
			"the board resolves to the root this harness installed");

	/* Fill up to one slot above the low-water mark. */
	for (i = 0U; i < (OWNER_REVOKE_MAX_RECORDS - OWNER_REVOKE_LOW_WATER - 1U); i++) {
		if (!revoke_nth(i)) {
			printf("[FAIL] revocation %u was refused with room left\n",
					(unsigned)i);
			g_failures++;
			break;
		}
	}
	CHECK(!report_contains("revocation slot(s) left"),
			"no warning yet with 9 slots left");

	/* T2-22: one more, and the warning has to appear. */
	CHECK(revoke_nth(OWNER_REVOKE_MAX_RECORDS - OWNER_REVOKE_LOW_WATER - 1U),
			"the revocation that reaches the low-water mark is accepted");
	CHECK(report_contains("Only 8 revocation slot(s) left"),
			"the boot line warns with 8 slots left");
	CHECK(report_contains("setowner --wipe"),
			"the warning names setowner --wipe as the way to empty them");

	/* Fill the rest. */
	for (i = OWNER_REVOKE_MAX_RECORDS - OWNER_REVOKE_LOW_WATER;
			i < OWNER_REVOKE_MAX_RECORDS; i++) {
		if (!revoke_nth(i)) {
			printf("[FAIL] revocation %u was refused with room left\n",
					(unsigned)i);
			g_failures++;
			break;
		}
	}
	CHECK(report_contains("0/96 revoke slot(s) free"),
			"all 96 revocation slots are used");
	CHECK(all_read_back_revoked(OWNER_REVOKE_MAX_RECORDS),
			"every one of the 96 leaves reads back as revoked");

	/* T2-23: the 97th. */
	memcpy(before, (const void *)OWNER_SEG_R_BASE, sizeof(before));
	bytes_before = fake_flash_bytes_written;

	CHECK(!revoke_nth(OWNER_REVOKE_MAX_RECORDS),
			"the 97th revocation is refused");
	CHECK(fake_flash_bytes_written == bytes_before,
			"the refusal wrote no bytes at all");
	CHECK(memcmp(before, (const void *)OWNER_SEG_R_BASE, sizeof(before)) == 0,
			"the revocation segment is byte-for-byte unchanged");

	/* The refusal must not have cost the board anything else either. */
	CHECK(memcmp(owner_slot_root(), g_root_pub, PUBKEY_SIZE) == 0,
			"the root in force is still the same after the refusal");

}

/*
 * T1-33. A deliberately messy area, then one compaction, then a rescan of the
 * written-back carry -- the round trip is the proof.
 *
 *   slot 0   generation 1, unsigned      the initial claim
 *   slot 1   wrong format_ver            garbage (skipped by the scan)
 *   slot 2   generation 2, signed by A   the handover to B (in force)
 *   slot 3   generation 3, UNSIGNED      what an attacker writes (must NOT be
 *                                        promoted by compaction)
 */
static void phase_compact(void)
{
	static owner_carry_t carry;
	owner_record_t expect;
	uint8_t leaf_kept_a[OWNER_REVOKE_PREFIX_LEN];
	uint8_t leaf_kept_b[OWNER_REVOKE_PREFIX_LEN];
	uint8_t self_named[OWNER_REVOKE_PREFIX_LEN];
	uint8_t *garbage;

	install_owner_record(0U, 1UL, g_root_pub, NULL);

	garbage = owner_slot_at(1U);
	memset(garbage, 0, OWNER_RECORD_SIZE);
	garbage[OFF_TYPE] = (uint8_t)'O';
	put_u16(&garbage[OFF_FORMAT_VER], (uint16_t)(FORMAT_VER - 1U));

	install_owner_record(2U, 2UL, g_next_pub, g_root_priv);
	install_owner_record(3U, 3UL, g_root_pub, NULL);   /* unsigned, not first */

	/* Two real leaves, plus one naming the root that ends up in force -- R4
	 * ignores that one, so compaction must not spend a slot carrying it. */
	leaf_name(1U, leaf_kept_a);
	leaf_name(2U, leaf_kept_b);
	memcpy(self_named, g_next_pub, OWNER_REVOKE_PREFIX_LEN);
	write_revoke_slot(0U, self_named);
	write_revoke_slot(1U, leaf_kept_a);
	write_revoke_slot(2U, leaf_kept_b);

	force_rescan();

	CHECK(memcmp(owner_slot_root(), g_next_pub, PUBKEY_SIZE) == 0,
			"the chain stops at the signed handover, not the unsigned record after it");

	owner_slot_build_carry(&carry);

	memcpy(&expect, owner_slot_at(2U), sizeof(expect));
	memset(expect.prev_sig, 0, sizeof(expect.prev_sig));
	CHECK(carry.has_owner == 1U, "the carry keeps an owner record");
	CHECK(memcmp(&carry.owner, &expect, sizeof(expect)) == 0,
			"it is the handover in force, with its signature stripped");
	CHECK(carry.revoke_count == 2U,
			"two revocations kept: the self-naming one is dropped");
	CHECK((memcmp(carry.revoke[0].leaf_prefix, leaf_kept_a, OWNER_REVOKE_PREFIX_LEN) == 0)
			&& (memcmp(carry.revoke[1].leaf_prefix, leaf_kept_b, OWNER_REVOKE_PREFIX_LEN) == 0),
			"and they are the two real leaves, in order");

	CHECK(write_file(COMPACTED_PATH, (const uint8_t *)&carry, sizeof(carry)),
			"the carry was handed to the read-back phase");
}

/* T1-33, second half: write the carry into an erased area and read it with a
 * fresh scan, as the next boot would. */
static void phase_compact_verify(void)
{
	static owner_carry_t carry;
	uint8_t leaf_kept_a[OWNER_REVOKE_PREFIX_LEN];
	uint8_t probe[64];

	if (!read_file(COMPACTED_PATH, (uint8_t *)&carry, sizeof(carry))) {
		printf("[FAIL] no %s -- the compact phase must run first\n", COMPACTED_PATH);
		g_failures++;
		return;
	}
	remove(COMPACTED_PATH);
	CHECK(owner_slot_write_carry(&carry), "the carry programs into an erased area");
	leaf_name(1U, leaf_kept_a);

	CHECK(memcmp(owner_slot_root(), g_next_pub, PUBKEY_SIZE) == 0,
			"after compaction the board still trusts the same root");
	CHECK(owner_slot_generation() == 2UL,
			"and is still at the generation the handover set");

	memset(probe, 0, sizeof(probe));
	memcpy(probe, leaf_kept_a, OWNER_REVOKE_PREFIX_LEN);
	CHECK(owner_slot_is_revoked(probe), "a kept revocation is still in effect");
	memset(probe, 0, sizeof(probe));
	leaf_name(50U, probe);
	CHECK(!owner_slot_is_revoked(probe), "a leaf nobody revoked is still fine");
}

/* The handover record the tool would ask for: generation `gen`, to `to`,
 * signed by `by_priv`. `rec` gets the unsigned form the board keeps. */
static void make_handover(uint32_t gen, const uint8_t to[PUBKEY_SIZE],
		const uint8_t *by_priv, uint8_t sig[64], owner_record_t *rec)
{
	uint8_t *r = (uint8_t *)rec;
	uint8_t digest[SHA256_DIGEST_SIZE];

	memset(r, 0, OWNER_RECORD_SIZE);
	r[OFF_TYPE] = (uint8_t)'O';
	r[OFF_RESERVED0] = 0U;
	put_u16(&r[OFF_FORMAT_VER], (uint16_t)FORMAT_VER);
	put_u32(&r[OFF_GENERATION], gen);
	put_u32(&r[OFF_FLAGS], 0UL);
	memcpy(&r[OFF_PAYLOAD], to, PUBKEY_SIZE);
	memcpy(&r[OFF_UID], test_machine_id(), IAP_MACHINE_ID_SIZE);
	sha256(r, OWNER_SIGNED_PREFIX_LEN, digest);
	if (uECC_sign(by_priv, digest, sizeof(digest), sig, uECC_secp256r1()) != 1) {
		printf("[FAIL] could not sign a handover -- harness problem\n");
		g_failures++;
	}
}

/* T2-24, first half: the wipe is judged before anything is erased. */
static void phase_wipe(void)
{
	static owner_carry_t carry;
	uint8_t sig[64];
	owner_record_t probe_rec;

	install_root();
	force_rescan();

	/* One revocation, so the wipe has something to drop. */
	CHECK(revoke_nth(7U), "a revocation was recorded before the wipe");

	make_handover(2UL, g_next_pub, g_root_priv, sig, &probe_rec);

	memset(&carry, 0x5A, sizeof(carry));
	CHECK(!owner_slot_build_wipe_carry(3UL, g_next_pub, sig, &carry),
			"a generation that is not one past the record in force is refused");
	CHECK(all_bytes_are((const uint8_t *)&carry, sizeof(carry), 0x5AU),
			"and the refusal did not touch the buffer");

	{
		uint8_t bad[64];

		memcpy(bad, sig, sizeof(bad));
		bad[0] = (uint8_t)(bad[0] ^ 0xFFU);
		CHECK(!owner_slot_build_wipe_carry(2UL, g_next_pub, bad, &carry),
				"a signature that does not verify is refused");
		CHECK(all_bytes_are((const uint8_t *)&carry, sizeof(carry), 0x5AU),
				"and that refusal did not touch the buffer either");
	}

	CHECK(owner_slot_build_wipe_carry(2UL, g_next_pub, sig, &carry),
			"a correctly signed handover is accepted");
	CHECK((carry.has_owner == 1U) && (memcmp(&carry.owner, &probe_rec, OWNER_RECORD_SIZE) == 0),
			"the carry holds that record, with the signature stripped");
	CHECK(carry.revoke_count == 0U, "and no revocations");

	CHECK(write_file(COMPACTED_PATH, (const uint8_t *)&carry, sizeof(carry)),
			"the carry was handed to the read-back phase");
}

/* T2-24, second half: the wiped area, read with a fresh scan. */
static void phase_wipe_verify(void)
{
	static owner_carry_t carry;
	uint8_t probe[64];

	if (!read_file(COMPACTED_PATH, (uint8_t *)&carry, sizeof(carry))) {
		printf("[FAIL] no %s -- the wipe phase must run first\n", COMPACTED_PATH);
		g_failures++;
		return;
	}
	remove(COMPACTED_PATH);
	CHECK(owner_slot_write_carry(&carry), "the carry programs into an erased area");

	CHECK(memcmp(owner_slot_root(), g_next_pub, PUBKEY_SIZE) == 0,
			"after the wipe the board trusts the new root");
	CHECK(owner_slot_generation() == 2UL,
			"at the generation the handover carried, not back at 1");

	memset(probe, 0, sizeof(probe));
	leaf_name(7U, probe);
	CHECK(!owner_slot_is_revoked(probe),
			"the revocation that existed before the wipe is gone");
	CHECK(report_contains("96/96 revoke slot(s) free"),
			"every revocation slot was reclaimed");
}

/* T2-31. A factory board: the area is erased, nothing is compiled in. */
static void phase_no_root(void)
{
	uint8_t sig[64];
	owner_record_t rec;
	uint8_t leaf[OWNER_REVOKE_PREFIX_LEN];
	bool already = false;

	CHECK(owner_slot_root() == NULL, "an erased area means no root");
	CHECK(report_contains("no root"), "and the boot line says so");

	make_handover(1UL, g_next_pub, g_root_priv, sig, &rec);
	CHECK(!owner_slot_set_owner(1UL, g_next_pub, sig),
			"setowner is refused: there is no owner to sign a handover");
	leaf_name(3U, leaf);
	sign_revocation(leaf, sig);
	CHECK(!owner_slot_revoke(leaf, sig, &already),
			"revoke is refused: there is no owner");
	CHECK(fake_flash_bytes_written == 0U, "neither refusal wrote a byte");

	CHECK(owner_slot_claim(g_root_pub), "takeown is accepted with no gate");
	CHECK(memcmp(owner_slot_root(), g_root_pub, PUBKEY_SIZE) == 0,
			"and the board now trusts that key");
	CHECK(owner_slot_generation() == 1UL, "at generation 1");
	CHECK(!owner_slot_claim(g_next_pub), "a second takeown is refused");
	CHECK(memcmp(owner_slot_root(), g_root_pub, PUBKEY_SIZE) == 0,
			"and the root did not change");
}

/* T2-32. */
static void phase_reset(void)
{
	install_root();
	force_rescan();

	CHECK(owner_slot_factory_reset(true), "the factory reset is accepted");
	CHECK(owner_slot_root() == NULL, "after it the board has no root");
	CHECK(owner_slot_claim(g_next_pub), "and takeown is accepted again");
	CHECK(memcmp(owner_slot_root(), g_next_pub, PUBKEY_SIZE) == 0,
			"with the new key in force");
	CHECK(owner_slot_generation() == 3UL,
			"at a generation past the reset, so the chain keeps its order");
}

/* T2-33. 40 handovers, each signed by the key it replaces. */
static void phase_rotate(void)
{
	static uint8_t pub[2][PUBKEY_SIZE];
	static uint8_t priv[2][32];
	uint32_t gen;
	uint32_t cur = 0U;
	uint8_t leaf[64];

	install_root();
	force_rescan();
	memcpy(pub[0], g_root_pub, PUBKEY_SIZE);
	memcpy(priv[0], g_root_priv, sizeof(priv[0]));

	CHECK(revoke_nth(5U), "a revocation is recorded before the handovers");

	for (gen = 2U; gen <= 41U; gen++) {
		uint32_t nxt = cur ^ 1U;
		uint8_t sig[64];
		owner_record_t rec;

		if (uECC_make_key(pub[nxt], priv[nxt], uECC_secp256r1()) != 1) {
			printf("[FAIL] could not generate a key pair -- harness problem\n");
			g_failures++;
			return;
		}
		make_handover(gen, pub[nxt], priv[cur], sig, &rec);
		if (!owner_slot_set_owner(gen, pub[nxt], sig)) {
			printf("[FAIL] handover to generation %u refused\n", (unsigned)gen);
			g_failures++;
			return;
		}
		cur = nxt;
	}

	CHECK(fake_reclaim_count >= 1U, "the full 'O' segment was reclaimed");
	CHECK(memcmp(owner_slot_root(), pub[cur], PUBKEY_SIZE) == 0,
			"the last key handed over is the root in force");
	CHECK(owner_slot_generation() == 41UL, "at generation 41");
	memset(leaf, 0, sizeof(leaf));
	leaf_name(5U, leaf);
	CHECK(owner_slot_is_revoked(leaf), "the revocation survived the reclaim");
}

int main(int argc, char **argv)
{
	const char *phase = (argc > 1) ? argv[1] : "capacity";

	uECC_set_rng(&test_rng);
	fake_flash_reset();

	if ((uECC_make_key(g_root_pub, g_root_priv, uECC_secp256r1()) != 1) ||
			(uECC_make_key(g_next_pub, g_next_priv, uECC_secp256r1()) != 1)) {
		printf("could not generate a key pair -- harness problem\n");
		return 2;
	}

	if (strcmp(phase, "capacity") == 0) {
		phase_capacity();
	} else if (strcmp(phase, "compact") == 0) {
		phase_compact();
	} else if (strcmp(phase, "compact-verify") == 0) {
		phase_compact_verify();
	} else if (strcmp(phase, "wipe") == 0) {
		phase_wipe();
	} else if (strcmp(phase, "wipe-verify") == 0) {
		phase_wipe_verify();
	} else if (strcmp(phase, "self-revoke") == 0) {
		phase_self_revoke();
	} else if (strcmp(phase, "no-root") == 0) {
		phase_no_root();
	} else if (strcmp(phase, "reset") == 0) {
		phase_reset();
	} else if (strcmp(phase, "rotate") == 0) {
		phase_rotate();
	} else {
		printf("unknown phase %s -- see PHASE_GROUPS in build.py\n", phase);
		return 2;
	}

	printf("\n%s (%d failure%s)\n", (g_failures == 0) ? "ALL PASS" : "FAILED",
			g_failures, (g_failures == 1) ? "" : "s");
	return (g_failures == 0) ? 0 : 1;
}
