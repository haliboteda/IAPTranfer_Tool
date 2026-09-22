/*
 * T2-22: the boot line starts warning with OWNER_REVOKE_LOW_WATER revocation
 *        slots left, and says nothing before that.
 * T2-23: the 97th revocation is refused and not one byte is written.
 *
 * Runs the REAL owner_slot.c from the bootloader over a RAM buffer that
 * stands in for the owner-record area, with a Flash_If_Write() that enforces
 * the H7's programming rules (stubs/fake_owner_flash.c). Records are built
 * here by byte offset rather than through owner_revoke_rec_t, so a layout
 * change on either side shows up as a failed assertion instead of being
 * silently followed.
 *
 * On a board these two cases would cost all 96 revocation slots permanently
 * -- nothing but a bootloader reflash reclaims them.
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

/* The first 'O' record: unsigned, which is legitimate exactly once (trust on
 * first use). Written straight into the buffer rather than through
 * owner_slot_claim(), because claiming needs a BOOT0 assertion this harness
 * has no way to make and the record it would produce is this one. */
static void install_root(void)
{
	uint8_t *r = fake_owner_area;   /* 'O' segment starts at offset 0 */

	memset(r, 0, OWNER_RECORD_SIZE);
	r[OFF_TYPE] = (uint8_t)'O';
	r[OFF_RESERVED0] = 0U;
	put_u16(&r[OFF_FORMAT_VER], (uint16_t)FORMAT_VER);
	put_u32(&r[OFF_GENERATION], 1UL);
	put_u32(&r[OFF_FLAGS], 0UL);
	memcpy(&r[OFF_PAYLOAD], g_root_pub, PUBKEY_SIZE);
	memcpy(&r[OFF_UID], test_machine_id(), IAP_MACHINE_ID_SIZE);
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

int main(void)
{
	uint32_t i;
	uint8_t before[OWNER_SEG_R_SIZE];
	uint32_t bytes_before;

	uECC_set_rng(&test_rng);
	fake_flash_reset();

	if (uECC_make_key(g_root_pub, g_root_priv, uECC_secp256r1()) != 1) {
		printf("could not generate a key pair -- harness problem\n");
		return 2;
	}
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
	CHECK(report_contains("does NOT free these slots"),
			"the warning says changing the root does not free slots");

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

	printf("\n%s (%d failure%s)\n", (g_failures == 0) ? "ALL PASS" : "FAILED",
			g_failures, (g_failures == 1) ? "" : "s");
	return (g_failures == 0) ? 0 : 1;
}
