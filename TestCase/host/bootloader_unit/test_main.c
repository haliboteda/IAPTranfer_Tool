/*
 * Host-side security test harness for the IAP certificate chain and
 * challenge-response protocol. Case H2.
 *
 * Compiles and runs the REAL bootloader source (sha256.c, iap_keyderive.c,
 * iap_cert.c, fw_verify.c + micro-ecc, iap_auth.c from
 * open_plc_cube_ide/IAPServer) natively on the PC, against a fake HAL
 * (stubs/hal_stub.c) and a fake owner slot (stubs/owner_slot_stub.c).
 * IAP_server.c's command parser is out of scope -- it needs the whole
 * USB/TCP/Flash stack.
 *
 * Every certificate and signature checked here came out of the shipping PC
 * tool (see gen_vectors.py / golden_vectors.h), so a pass means the two
 * implementations agree on the wire format rather than each being internally
 * consistent.
 *
 * Build & run: python build.py
 */

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include "iap_auth.h"
#include "iap_cert.h"
#include "iap_keyderive.h"
#include "fw_verify.h"
#include "sha256.h"
#include "hal_stub.h"
#include "owner_slot_stub.h"
#include "rtc.h"
#include "golden_vectors.h"

static int g_failures = 0;

#define CHECK(cond, desc) do { \
	if (cond) { printf("[PASS] %s\n", (desc)); } \
	else { printf("[FAIL] %s\n", (desc)); g_failures++; } \
} while (0)

static const iap_cert_t *as_cert(const uint8_t *bytes)
{
	return (const iap_cert_t *)bytes;
}

/* The hash a firmware signature is actually made over. */
static void golden_image_hash(uint8_t out[32])
{
	sha256(golden_image_blob, (uint32_t)sizeof(golden_image_blob), out);
}

/* Puts the fake board on the golden root, as the golden device, at the tick
 * the golden nonce was computed for. */
static void arrange_golden_board(void)
{
	test_hal_reset();
	test_hal_set_uid(GOLDEN_UID0, GOLDEN_UID1, GOLDEN_UID2);
	test_hal_set_tick(GOLDEN_TICK);
	test_owner_set_root(golden_root_pub);
}

/* Test 1: the crypto primitives self-check against FIPS 180-4 / RFC 4231
 * vectors -- if this fails, nothing else in this file can be trusted. */
static void test_crypto_selftest(void)
{
	CHECK(sha256_selftest(), "sha256_selftest() passes known-answer vectors");
}

/* Test 2: machine-ID hex format matches what discovery/getuid must report:
 * uppercase, UIDW2||UIDW1||UIDW0. */
static void test_machine_id_hex_format(void)
{
	char hex_out[IAP_MACHINE_ID_HEX_LEN + 1U];

	test_hal_set_uid(0x01234567U, 0x89ABCDEFU, 0xDEADBEEFU);
	iap_keyderive_get_machine_id_hex(hex_out);
	CHECK(strcmp(hex_out, "DEADBEEF89ABCDEF01234567") == 0,
			"machine_id_hex is uppercase UIDW2||UIDW1||UIDW0");
}

/* Test 3: the certificate is three fields at fixed offsets and nothing else.
 * A compiler that pads it, or a field that moves, silently stops matching
 * what the tool puts on the wire -- and every signature check would still
 * "work", just over different bytes. */
static void test_cert_layout(void)
{
	const iap_cert_t *cert = as_cert(golden_cert_delegated);

	CHECK(sizeof(iap_cert_t) == IAP_CERT_SIZE, "iap_cert_t is exactly 132 bytes");
	CHECK(IAP_CERT_SIGNED_LEN == 68U, "the root signature covers leaf_pubkey||serial");
	CHECK(memcmp(cert->leaf_pubkey, golden_leaf_pub, 64) == 0,
			"leaf_pubkey lands at offset 0 of the tool's certificate");
	CHECK(cert->serial >= 1U, "serial decodes as a little-endian uint32");
	CHECK(memcmp(golden_cert_delegated + 68, cert->root_sig, 64) == 0,
			"root_sig lands at offset 68");
}

/* Test 4: a certificate is accepted exactly when the root this board trusts
 * signed it -- delegated and self-signed alike, since there is no self-signed
 * branch in the code. */
static void test_cert_verify(void)
{
	CHECK(iap_cert_verify(as_cert(golden_cert_delegated), golden_root_pub),
			"a delegated certificate verifies against its root");
	CHECK(iap_cert_verify(as_cert(golden_cert_self), golden_root_pub),
			"simple mode: a self-signed certificate takes the same path and verifies");
	CHECK(!iap_cert_verify(as_cert(golden_cert_foreign), golden_root_pub),
			"a certificate signed by another root is rejected");
	CHECK(!iap_cert_verify(as_cert(golden_cert_delegated), golden_foreign_pub),
			"the same certificate is rejected once the board trusts a different root");
}

/* Test 5: tampering anywhere in the signed prefix must break the root
 * signature. Swapping the leaf key is the attack the signature exists to
 * stop; the serial matters because C12 revocation will name certificates by
 * it, and a serial that can be edited after issuance revokes nothing. */
static void test_cert_tamper(void)
{
	iap_cert_t tampered;

	memcpy(&tampered, golden_cert_delegated, IAP_CERT_SIZE);
	memcpy(tampered.leaf_pubkey, golden_foreign_pub, 64);
	CHECK(!iap_cert_verify(&tampered, golden_root_pub),
			"substituting another leaf key breaks the root signature");

	memcpy(&tampered, golden_cert_delegated, IAP_CERT_SIZE);
	tampered.serial ^= 1U;
	CHECK(!iap_cert_verify(&tampered, golden_root_pub),
			"editing the serial breaks the root signature");

	memcpy(&tampered, golden_cert_delegated, IAP_CERT_SIZE);
	tampered.root_sig[0] ^= 0x01U;
	CHECK(!iap_cert_verify(&tampered, golden_root_pub),
			"a corrupted root signature is rejected");
}

/* Test 6: the two-step image check. Both halves must hold -- a valid
 * certificate says nothing about who signed this image, and a valid image
 * signature says nothing if the leaf was never certified. */
static void test_cert_verify_image(void)
{
	uint8_t hash[32];

	golden_image_hash(hash);

	CHECK(iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub),
			"certified leaf + its own signature over the image is accepted");
	CHECK(iap_cert_verify_image(hash, golden_image_sig_root,
			as_cert(golden_cert_self), golden_root_pub),
			"simple mode: root's own signature under a self-signed certificate is accepted");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_foreign,
			as_cert(golden_cert_foreign), golden_root_pub),
			"an uncertified leaf is rejected even though it did sign the image");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_foreign,
			as_cert(golden_cert_delegated), golden_root_pub),
			"a certified leaf does not vouch for an image somebody else signed");

	hash[0] ^= 0x01U;
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub),
			"the signature does not carry over to a different image hash");
}

/* Test 7: the handover the whole scheme rests on -- change the root and
 * firmware certified by the old one stops verifying, with nothing else
 * touched. This is what makes setowner retroactively invalidate an installed
 * image. */
static void test_root_change_invalidates(void)
{
	uint8_t hash[32];

	golden_image_hash(hash);
	CHECK(iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub),
			"before the handover the installed image verifies");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_foreign_pub),
			"after a handover to another root the same image no longer verifies");
}

/* Test 8: a full challenge-response, with the nonce signature produced by the
 * PC tool. Also pins the nonce itself: counter||UIDW0||tick||0. */
static void test_challenge_response(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	const char *msg = GOLDEN_AUTH_MSG;
	bool accepted, replayed;

	arrange_golden_board();
	/* RTC_BKP_DR1 starts at 0 (test_hal_reset), so next_counter() -> 1,
	 * which is the counter the golden nonce was signed for. */
	iap_auth_issue_challenge(nonce_hex);
	CHECK(strcmp(nonce_hex, "01000000674523018813000000000000") == 0,
			"nonce is counter||UIDW0||tick||0000, little-endian");

	accepted = iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_leaf);
	CHECK(accepted, "a challenge signed by a certified leaf is accepted");

	replayed = iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_leaf);
	CHECK(!replayed, "replaying the same (nonce, certificate, signature) is rejected");
}

/* Test 9: holding a private key is not enough -- the certificate naming it
 * has to come from this board's root. */
static void test_uncertified_signer_rejected(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	const char *msg = GOLDEN_AUTH_MSG;

	arrange_golden_board();
	iap_auth_issue_challenge(nonce_hex);
	CHECK(!iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_foreign), golden_auth_sig_foreign),
			"a correctly signed challenge under an uncertified certificate is rejected");

	arrange_golden_board();
	iap_auth_issue_challenge(nonce_hex);
	CHECK(!iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_foreign),
			"a certified certificate with somebody else's signature is rejected");
}

/* Test 10: a challenge answered after IAP_AUTH_NONCE_TTL_MS is refused even
 * though the signature is perfect. */
static void test_nonce_expiry(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	const char *msg = GOLDEN_AUTH_MSG;

	arrange_golden_board();
	iap_auth_issue_challenge(nonce_hex);
	test_hal_set_tick(GOLDEN_TICK + IAP_AUTH_NONCE_TTL_MS + 1U);

	CHECK(!iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_leaf),
			"a correctly signed but expired nonce (>30s old) is rejected");
}

/* Test 11: an answer with no challenge behind it. */
static void test_no_pending_nonce(void)
{
	const char *msg = GOLDEN_AUTH_MSG;

	arrange_golden_board();
	CHECK(!iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_leaf),
			"an answer arriving before any challenge was issued is rejected");
}

int main(void)
{
	test_crypto_selftest();
	test_machine_id_hex_format();
	test_cert_layout();
	test_cert_verify();
	test_cert_tamper();
	test_cert_verify_image();
	test_root_change_invalidates();
	test_challenge_response();
	test_uncertified_signer_rejected();
	test_nonce_expiry();
	test_no_pending_nonce();

	printf("\n%s (%d failure(s))\n", g_failures == 0 ? "ALL PASS" : "FAILED", g_failures);
	return g_failures == 0 ? 0 : 1;
}
