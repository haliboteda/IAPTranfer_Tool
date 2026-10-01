/*
 * Host-side security test harness for the IAP certificate chain and
 * challenge-response protocol. Case T1-16.
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
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include "iap_auth.h"
#include "iap_cert.h"
#include "iap_keyderive.h"
#include "fw_verify.h"
#include "sha256.h"
#include "net_rand.h"
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

/* Test 3: the certificate is two fields at fixed offsets and nothing else.
 * A compiler that pads it, or a field that moves, silently stops matching
 * what the tool puts on the wire -- and every signature check would still
 * "work", just over different bytes. */
static void test_cert_layout(void)
{
	const iap_cert_t *cert = as_cert(golden_cert_delegated);

	CHECK(sizeof(iap_cert_t) == IAP_CERT_SIZE, "iap_cert_t is exactly 128 bytes");
	CHECK(IAP_CERT_SIGNED_LEN == 64U, "the root signature covers leaf_pubkey only");
	CHECK(memcmp(cert->leaf_pubkey, golden_leaf_pub, 64) == 0,
			"leaf_pubkey lands at offset 0 of the tool's certificate");
	CHECK(memcmp(golden_cert_delegated + 64, cert->root_sig, 64) == 0,
			"root_sig lands at offset 64");
}

/* Test 4: a certificate is accepted exactly when the root this board trusts
 * signed it and the leaf has not been revoked -- delegated and self-signed
 * alike, since there is no self-signed branch in the code. */
static void test_cert_verify(void)
{
	CHECK(iap_cert_verify(as_cert(golden_cert_delegated), golden_root_pub, false),
			"a delegated certificate verifies against its root");
	CHECK(iap_cert_verify(as_cert(golden_cert_self), golden_root_pub, false),
			"simple mode: a self-signed certificate takes the same path and verifies");
	CHECK(!iap_cert_verify(as_cert(golden_cert_foreign), golden_root_pub, false),
			"a certificate signed by another root is rejected");
	CHECK(!iap_cert_verify(as_cert(golden_cert_delegated), golden_foreign_pub, false),
			"the same certificate is rejected once the board trusts a different root");
}

/* Test 5: revocation short-circuits verification even when the signature and
 * the root are both perfect -- and only for the leaf actually named. */
static void test_cert_revoked(void)
{
	CHECK(iap_cert_verify(as_cert(golden_cert_delegated), golden_root_pub, false),
			"CONTROL: not revoked, and everything else about it is unchanged from "
			"test_cert_verify -- so the next line failing means revocation, not a "
			"broken signature");
	CHECK(!iap_cert_verify(as_cert(golden_cert_delegated), golden_root_pub, true),
			"a certificate whose leaf has been revoked is rejected regardless of signature");
	CHECK(iap_cert_verify(as_cert(golden_cert_self), golden_root_pub, false),
			"revoking one leaf does not touch a certificate that was never revoked");
}

/* Test 6: tampering anywhere in the signed prefix must break the root
 * signature. Swapping the leaf key is the attack the signature exists to
 * stop. */
static void test_cert_tamper(void)
{
	iap_cert_t tampered;

	memcpy(&tampered, golden_cert_delegated, IAP_CERT_SIZE);
	memcpy(tampered.leaf_pubkey, golden_foreign_pub, 64);
	CHECK(!iap_cert_verify(&tampered, golden_root_pub, false),
			"substituting another leaf key breaks the root signature");

	memcpy(&tampered, golden_cert_delegated, IAP_CERT_SIZE);
	tampered.root_sig[0] ^= 0x01U;
	CHECK(!iap_cert_verify(&tampered, golden_root_pub, false),
			"a corrupted root signature is rejected");
}

/* Test 7: the two-step image check. Both halves must hold -- a valid
 * certificate says nothing about who signed this image, and a valid image
 * signature says nothing if the leaf was never certified (or has since been
 * revoked). */
static void test_cert_verify_image(void)
{
	uint8_t hash[32];

	golden_image_hash(hash);

	CHECK(iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub, false),
			"certified leaf + its own signature over the image is accepted");
	CHECK(iap_cert_verify_image(hash, golden_image_sig_root,
			as_cert(golden_cert_self), golden_root_pub, false),
			"simple mode: root's own signature under a self-signed certificate is accepted");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_foreign,
			as_cert(golden_cert_foreign), golden_root_pub, false),
			"an uncertified leaf is rejected even though it did sign the image");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_foreign,
			as_cert(golden_cert_delegated), golden_root_pub, false),
			"a certified leaf does not vouch for an image somebody else signed");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub, true),
			"a leaf revoked after installing an image no longer boots it -- this is "
			"what makes revocation retroactive, not just block future uploads");

	hash[0] ^= 0x01U;
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub, false),
			"the signature does not carry over to a different image hash");
}

/* Test 8: the handover the whole scheme rests on -- change the root and
 * firmware certified by the old one stops verifying, with nothing else
 * touched. This is what makes setowner retroactively invalidate an installed
 * image. */
static void test_root_change_invalidates(void)
{
	uint8_t hash[32];

	golden_image_hash(hash);
	CHECK(iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_root_pub, false),
			"before the handover the installed image verifies");
	CHECK(!iap_cert_verify_image(hash, golden_image_sig_leaf,
			as_cert(golden_cert_delegated), golden_foreign_pub, false),
			"after a handover to another root the same image no longer verifies");
}

/* Test 8: a full challenge-response, with the nonce signature produced by the
 * PC tool. The nonce is whatever the RNG hands over, so the stub is told to
 * hand over exactly the 16 bytes the golden signature was made for. */
static void test_challenge_response(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	const char *msg = GOLDEN_AUTH_MSG;
	bool accepted, replayed;
	/* Little-endian words spelling 01000000 67452301 88130000 00000000. */
	static const uint32_t golden_nonce_words[4] = {
		0x00000001U, 0x01234567U, 0x00001388U, 0x00000000U
	};

	arrange_golden_board();
	test_hal_set_rng_words(golden_nonce_words, 4U);
	CHECK(iap_auth_issue_challenge(nonce_hex), "a challenge is issued");
	CHECK(strcmp(nonce_hex, "01000000674523018813000000000000") == 0,
			"the nonce is the 16 bytes the RNG handed over, little-endian");

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

/* Test 9b: a revoked leaf cannot open a session either -- revocation has to
 * stop BOTH doors (upload and session auth), not just the one this file's
 * other tests happen to exercise most. */
static void test_revoked_leaf_rejected_in_session_auth(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	const char *msg = GOLDEN_AUTH_MSG;

	arrange_golden_board();
	test_owner_revoke(golden_leaf_pub);
	iap_auth_issue_challenge(nonce_hex);
	CHECK(!iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_leaf),
			"a session challenge signed by a revoked leaf is rejected");
	test_owner_clear_revocations();
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

/* Test 13: an RNG that cannot deliver must produce no challenge at all. The
 * alternative -- falling back to whatever the data register held -- is a fixed
 * nonce, which is worse than the counter this replaced. */
static void test_rng_failure_issues_nothing(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	const char *msg = GOLDEN_AUTH_MSG;

	arrange_golden_board();
	test_hal_set_rng_fail(1);
	CHECK(!iap_auth_issue_challenge(nonce_hex), "no challenge is issued when the RNG fails");

	/* And the previous nonce must not still be accepting answers. */
	CHECK(!iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg),
			as_cert(golden_cert_delegated), golden_auth_sig_leaf),
			"a failed challenge leaves no nonce pending");
	test_hal_set_rng_fail(0);
}

/* Test 14: lwIP's LWIP_RAND() is rand(), so the seed is what makes DHCP xids
 * and ephemeral ports differ per boot and per board (decision 67). */
static void test_net_rand_seed(void)
{
	const uint32_t word = 0x5EED1234U;
	int expected;

	test_hal_reset();
	srand(word);
	expected = rand();
	srand(1U);
	test_hal_set_rng_words(&word, 1U);
	net_rand_seed();
	CHECK(rand() == expected, "net_rand_seed() seeds rand() with the RNG word");

	srand(42U);
	expected = rand();
	srand(42U);
	test_hal_set_rng_fail(1);
	net_rand_seed();
	CHECK(rand() == expected, "a failed RNG leaves rand() as it was");
	test_hal_set_rng_fail(0);
}

/* Test 15: the TCP initial sequence number is the RNG word, and still
 * something when the RNG fails. */
static void test_net_rand_tcp_isn(void)
{
	const uint32_t word = 0x15A15A15U;
	uint32_t expected;

	test_hal_reset();
	test_hal_set_rng_words(&word, 1U);
	CHECK(net_rand_tcp_isn() == word, "net_rand_tcp_isn() returns the RNG word");

	srand(7U);
	expected = (uint32_t)rand();
	srand(7U);
	test_hal_set_rng_fail(1);
	CHECK(net_rand_tcp_isn() == expected, "net_rand_tcp_isn() falls back to rand() when the RNG fails");
	test_hal_set_rng_fail(0);
}

int main(void)
{
	test_crypto_selftest();
	test_machine_id_hex_format();
	test_cert_layout();
	test_cert_verify();
	test_cert_revoked();
	test_cert_tamper();
	test_cert_verify_image();
	test_root_change_invalidates();
	test_challenge_response();
	test_uncertified_signer_rejected();
	test_revoked_leaf_rejected_in_session_auth();
	test_nonce_expiry();
	test_no_pending_nonce();
	test_rng_failure_issues_nothing();
	test_net_rand_seed();
	test_net_rand_tcp_isn();

	printf("\n%s (%d failure(s))\n", g_failures == 0 ? "ALL PASS" : "FAILED", g_failures);
	return g_failures == 0 ? 0 : 1;
}
