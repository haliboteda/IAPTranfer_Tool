/*
 * T2-34: a sector-15 reclaim cut short by a power loss.
 *
 *   backup copy intact    the next boot finishes the reclaim: the root and the
 *                         firmware metadata are back
 *   backup copy gone      the board comes up with no root, never with a root
 *   (battery dead, or     it did not have
 *    copy corrupted)
 *
 * Plus the two boots with no reclaim in flight: a factory sector (calibration
 * only) is marked without an erase, and a sector in the old layout is rebuilt
 * keeping calibration.
 *
 * Runs the REAL bootloader_state.c, bkp_stash.c and owner_slot.c over RAM
 * buffers (stubs/). One phase per process, because both modules cache what
 * they scanned; the state between phases goes through STATE_PATH.
 *
 * Build & run: python build.py
 */

#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_s15.h"
#include "bootloader_state.h"
#include "owner_slot.h"
#include "iap_keyderive.h"
#include "iap_keyderive_stub.h"

extern jmp_buf fake_cut_jmp;
extern int     fake_cut_at;
extern int     fake_ops;
extern int     fake_erases;

#define STATE_PATH   "s15_state.bin"

#define CALIB_OFF    0U
#define ROOT_OFF     (8U * 1024U)
#define META_OFF     (16U * 1024U)
#define MARKER_OFF   (FAKE_SECTOR_SIZE - 32U)
#define META_BYTES   224U
#define APP_SIZE     0x12345U

static int g_failures = 0;

#define CHECK(cond, desc) do { \
	if (cond) { printf("[PASS] %s\n", (desc)); } \
	else { printf("[FAIL] %s\n", (desc)); g_failures++; } \
} while (0)

static uint8_t g_root[64];
static uint8_t g_leaf[64];

static void keys(void)
{
	uint32_t i;

	for (i = 0U; i < 64U; i++) {
		g_root[i] = (uint8_t)(0xA0U + i);
		g_leaf[i] = (uint8_t)(0x30U + i);
	}
}

static void put_u32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
	p[2] = (uint8_t)(v >> 16);
	p[3] = (uint8_t)(v >> 24);
}

static void write_calib(void)
{
	uint32_t i;

	for (i = 0U; i < 64U; i++) {
		fake_sector[CALIB_OFF + i] = (uint8_t)(0x11U + i);
	}
}

static bool calib_intact(void)
{
	uint32_t i;

	for (i = 0U; i < 64U; i++) {
		if (fake_sector[CALIB_OFF + i] != (uint8_t)(0x11U + i)) {
			return false;
		}
	}
	return true;
}

static void write_marker(void)
{
	put_u32(&fake_sector[MARKER_OFF], 0x4C353153UL);   /* "S15L" */
	put_u32(&fake_sector[MARKER_OFF + 4U], 1U);
	memset(&fake_sector[MARKER_OFF + 8U], 0, 24U);
}

static bool marker_valid(void)
{
	static const uint8_t magic[4] = { 0x53, 0x31, 0x35, 0x4C };

	return memcmp(&fake_sector[MARKER_OFF], magic, 4U) == 0;
}

/* A claimed board in the current layout: calibration, one owner record, one
 * revocation, one metadata record, the marker. */
static void arrange_claimed(void)
{
	owner_record_t o;
	owner_revoke_rec_t r;
	uint8_t *m = &fake_sector[META_OFF];

	memset(fake_sector, 0xFF, sizeof(fake_sector));
	memset(fake_bkpsram, 0, sizeof(fake_bkpsram));
	write_calib();

	memset(&o, 0, sizeof(o));
	o.type = (uint8_t)OWNER_RECORD_TYPE;
	o.format_ver = (uint16_t)OWNER_FORMAT_VER;
	o.generation = 1U;
	memcpy(o.root_pubkey, g_root, 64U);
	memcpy(o.uid, test_machine_id(), IAP_MACHINE_ID_SIZE);
	memcpy(&fake_sector[ROOT_OFF], &o, sizeof(o));

	memset(&r, 0, sizeof(r));
	r.type = (uint8_t)OWNER_RECORD_TYPE_REVOKE;
	r.format_ver = (uint16_t)OWNER_FORMAT_VER;
	memcpy(r.uid, test_machine_id(), IAP_MACHINE_ID_SIZE);
	memcpy(r.leaf_prefix, g_leaf, OWNER_REVOKE_PREFIX_LEN);
	memcpy(&fake_sector[ROOT_OFF + OWNER_SEG_O_SIZE], &r, sizeof(r));

	memset(m, 0, META_BYTES);
	m[0] = 0x4DU;                  /* 'M' */
	m[1] = (uint8_t)IAP_METADATA_SLOTS;
	put_u32(&m[8], APP_SIZE);      /* app_size */

	write_marker();
}

static bool save_state(void)
{
	FILE *f = fopen(STATE_PATH, "wb");
	bool ok;

	if (f == NULL) {
		return false;
	}
	ok = (fwrite(fake_sector, 1U, sizeof(fake_sector), f) == sizeof(fake_sector))
			&& (fwrite(fake_bkpsram, 1U, sizeof(fake_bkpsram), f) == sizeof(fake_bkpsram));
	fclose(f);
	return ok;
}

static bool load_state(void)
{
	FILE *f = fopen(STATE_PATH, "rb");
	bool ok;

	if (f == NULL) {
		return false;
	}
	ok = (fread(fake_sector, 1U, sizeof(fake_sector), f) == sizeof(fake_sector))
			&& (fread(fake_bkpsram, 1U, sizeof(fake_bkpsram), f) == sizeof(fake_bkpsram));
	fclose(f);
	return ok;
}

static bool stash_present(void)
{
	static const uint8_t magic[4] = { 0x53, 0x54, 0x53, 0x48 };   /* "STSH" */

	return memcmp(fake_bkpsram, magic, 4U) == 0;
}

/* Cut the reclaim a full 'O' segment would trigger right before flash
 * operation `cut_at` (0 = the erase). */
static void phase_cut(int cut_at)
{
	static owner_carry_t carry;

	arrange_claimed();
	bootloader_state_init();
	CHECK(owner_slot_root() != NULL, "the arranged board has a root");

	owner_slot_build_carry(&carry);
	fake_ops = 0;
	fake_cut_at = cut_at;
	if (setjmp(fake_cut_jmp) == 0) {
		(void)bootloader_state_reclaim(&carry);
		printf("reclaim ran to completion (%d flash operations)\n", fake_ops);
	} else {
		printf("power cut before flash operation %d\n", cut_at);
	}
	fake_cut_at = -1;
	CHECK(save_state(), "state saved for the next boot");
}

static void phase_battery_dead(void)
{
	CHECK(load_state(), "state loaded");
	memset(fake_bkpsram, 0, sizeof(fake_bkpsram));
	CHECK(save_state(), "backup SRAM lost with the battery");
}

static void phase_corrupt(void)
{
	CHECK(load_state(), "state loaded");
	fake_bkpsram[100] ^= 0x01U;
	CHECK(save_state(), "one bit of the backup copy flipped");
}

/* The next boot. `expect_root`: 1 = the original root, 0 = no root. */
static void phase_boot(int expect_root, int expect_calib)
{
	iap_fw_metadata_t meta;
	uint8_t probe[64];

	CHECK(load_state(), "state loaded");
	remove(STATE_PATH);
	fake_ops = 0;
	fake_erases = 0;

	bootloader_state_init();

	CHECK(marker_valid(), "after the boot the sector carries the layout marker");
	CHECK(!stash_present(), "and no backup copy is left behind");
	if (expect_root != 0) {
		CHECK((owner_slot_root() != NULL) && (memcmp(owner_slot_root(), g_root, 64U) == 0),
				"the root is back");
		CHECK(bootloader_state_get_metadata(&meta) && (meta.app_size == APP_SIZE),
				"and the firmware metadata is back");
		memset(probe, 0, sizeof(probe));
		memcpy(probe, g_leaf, OWNER_REVOKE_PREFIX_LEN);
		CHECK(owner_slot_is_revoked(probe), "and the revocation");
	} else {
		CHECK(owner_slot_root() == NULL, "the board has no root");
		CHECK(!bootloader_state_get_metadata(&meta), "and no firmware metadata");
	}
	if (expect_calib != 0) {
		CHECK(calib_intact(), "calibration is intact");
	}
}

/* A factory sector: calibration and nothing else. */
static void phase_fresh(void)
{
	memset(fake_sector, 0xFF, sizeof(fake_sector));
	memset(fake_bkpsram, 0, sizeof(fake_bkpsram));
	write_calib();
	fake_erases = 0;

	bootloader_state_init();

	CHECK(fake_erases == 0, "a factory sector is not erased");
	CHECK(marker_valid(), "it gets the layout marker");
	CHECK(calib_intact(), "calibration is untouched");
	CHECK(owner_slot_root() == NULL, "the board has no root");
	CHECK(owner_slot_claim(g_root), "and the first takeown is accepted");
}

/* The layout before decision 72: metadata straight after calibration, no
 * marker. */
static void phase_legacy(void)
{
	uint8_t *m = &fake_sector[ROOT_OFF];

	memset(fake_sector, 0xFF, sizeof(fake_sector));
	memset(fake_bkpsram, 0, sizeof(fake_bkpsram));
	write_calib();
	memset(m, 0, META_BYTES);
	m[0] = 0x4DU;
	m[1] = (uint8_t)IAP_METADATA_SLOTS;
	fake_erases = 0;

	bootloader_state_init();

	CHECK(fake_erases == 1, "an old-layout sector is rebuilt once");
	CHECK(marker_valid(), "with the layout marker");
	CHECK(calib_intact(), "keeping calibration");
	CHECK(owner_slot_root() == NULL, "the board has no root");
}

int main(int argc, char **argv)
{
	const char *phase = (argc > 1) ? argv[1] : "";

	keys();
	if (strcmp(phase, "cut") == 0 && argc > 2) {
		phase_cut(atoi(argv[2]));
	} else if (strcmp(phase, "battery-dead") == 0) {
		phase_battery_dead();
	} else if (strcmp(phase, "corrupt") == 0) {
		phase_corrupt();
	} else if (strcmp(phase, "boot") == 0 && argc > 3) {
		phase_boot(atoi(argv[2]), atoi(argv[3]));
	} else if (strcmp(phase, "fresh") == 0) {
		phase_fresh();
	} else if (strcmp(phase, "legacy") == 0) {
		phase_legacy();
	} else {
		printf("unknown phase -- see build.py\n");
		return 2;
	}

	printf("\n%s (%d failure%s)\n", (g_failures == 0) ? "ALL PASS" : "FAILED",
			g_failures, (g_failures == 1) ? "" : "s");
	return (g_failures == 0) ? 0 : 1;
}
