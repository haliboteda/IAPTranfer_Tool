/*
 * Host-side security test harness for the IAP challenge-response protocol.
 *
 * Compiles and runs the REAL bootloader source files (sha256.c,
 * iap_keyderive.c, iap_auth.c from open_plc_cube_ide/IAPServer) natively on
 * the PC, against a fake HAL (stubs/hal_stub.c) instead of real STM32
 * hardware. Scope is deliberately just the auth/crypto core -- not
 * IAP_server.c's command parser, which needs the full USB/TCP/Flash stack
 * and isn't security-critical in the same way.
 *
 * Test 4 below is the important one: its expected values were computed
 * independently in Go, using the exact same construction the PC tool
 * (IAPTranfer_Tool/iapcrypto) uses. If this test passes, the bootloader's C
 * implementation and the PC tool's Go implementation are proven wire-
 * compatible for that input, not just "each internally consistent".
 *
 * Build & run: see build.sh (bash) or build.ps1 (PowerShell).
 */

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include "iap_auth.h"
#include "iap_keyderive.h"
#include "sha256.h"
#include "hal_stub.h"
#include "rtc.h"

static int g_failures = 0;

#define CHECK(cond, desc) do { \
	if (cond) { printf("[PASS] %s\n", (desc)); } \
	else { printf("[FAIL] %s\n", (desc)); g_failures++; } \
} while (0)

static void hex_encode(const uint8_t *bytes, uint32_t len, char *out)
{
	static const char digits[] = "0123456789abcdef";
	uint32_t i;
	for (i = 0; i < len; i++) {
		out[i * 2U] = digits[bytes[i] >> 4];
		out[i * 2U + 1U] = digits[bytes[i] & 0x0FU];
	}
	out[len * 2U] = '\0';
}

static bool hex_decode(const char *hex, uint8_t *out, uint32_t out_len)
{
	uint32_t i;
	if (strlen(hex) != out_len * 2U) {
		return false;
	}
	for (i = 0; i < out_len; i++) {
		unsigned int byte;
		if (sscanf(hex + i * 2U, "%2x", &byte) != 1) {
			return false;
		}
		out[i] = (uint8_t)byte;
	}
	return true;
}

/* Test 1: the crypto primitives self-check against FIPS 180-4 / RFC 4231
 * vectors -- if this fails, nothing else in this file can be trusted. */
static void test_crypto_selftest(void)
{
	CHECK(sha256_selftest(), "sha256_selftest() passes known-answer vectors");
}

/* Test 2: same UID always derives the same key; a different UID (even by
 * one bit) must derive a different key -- the actual property the whole
 * per-device scheme depends on. */
static void test_key_derivation_determinism_and_uniqueness(void)
{
	uint8_t k1[IAP_DEVICE_KEY_SIZE], k2[IAP_DEVICE_KEY_SIZE], k3[IAP_DEVICE_KEY_SIZE];

	test_hal_reset();
	test_hal_set_uid(0x11111111U, 0x22222222U, 0x33333333U);
	iap_keyderive_get_device_key(k1);
	iap_keyderive_get_device_key(k2);
	CHECK(memcmp(k1, k2, sizeof(k1)) == 0, "device key is deterministic for a fixed UID");

	test_hal_set_uid(0x11111111U, 0x22222222U, 0x33333334U); /* last nibble differs */
	iap_keyderive_get_device_key(k3);
	CHECK(memcmp(k1, k3, sizeof(k1)) != 0, "one-bit UID change derives a different device key");
}

/* Test 3: machine-ID hex format matches what discovery/getuid must report:
 * uppercase, UIDW2||UIDW1||UIDW0. */
static void test_machine_id_hex_format(void)
{
	char hex_out[IAP_MACHINE_ID_HEX_LEN + 1U];

	test_hal_set_uid(0x01234567U, 0x89ABCDEFU, 0xDEADBEEFU);
	iap_keyderive_get_machine_id_hex(hex_out);
	CHECK(strcmp(hex_out, "DEADBEEF89ABCDEF01234567") == 0,
			"machine_id_hex is uppercase UIDW2||UIDW1||UIDW0");
}

/* Test 4: cross-language golden vector. Every expected value here was
 * computed independently with IAPTranfer_Tool/iapcrypto in Go (see the
 * conversation this harness came out of) for:
 *   uid0=0x01234567 uid1=0x89ABCDEF uid2=0xDEADBEEF, counter=1, tick=5000,
 *   msg="flash 1024 deadbeef abcd1234"
 * A match here means the bootloader's C auth code and the PC tool's Go
 * auth code agree byte-for-byte, not just "each passes its own tests". */
static void test_golden_cross_language_vector(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	uint8_t device_key[IAP_DEVICE_KEY_SIZE];
	char device_key_hex[IAP_DEVICE_KEY_SIZE * 2U + 1U];
	uint8_t golden_hmac[IAP_AUTH_HMAC_SIZE];
	const char *msg = "flash 1024 deadbeef abcd1234";
	bool accepted, replayed;

	test_hal_reset();
	test_hal_set_uid(0x01234567U, 0x89ABCDEFU, 0xDEADBEEFU);
	test_hal_set_tick(5000U);
	/* RTC_BKP_DR1 starts at 0 (test_hal_reset), so next_counter() -> 1,
	 * matching the golden vector's counter=1. */

	iap_auth_issue_challenge(nonce_hex);
	CHECK(strcmp(nonce_hex, "01000000674523018813000000000000") == 0,
			"nonce matches Go-computed golden nonce (counter||uidW0||tick||0000)");

	iap_keyderive_get_device_key(device_key);
	hex_encode(device_key, sizeof(device_key), device_key_hex);
	CHECK(strcmp(device_key_hex,
			"0354b4d5084eaa033950487d999bdcd8354d2d2bf42387b8a6b0c6513a670b2f") == 0,
			"device key matches Go-computed golden HMAC-SHA256(password, uid)");

	hex_decode("f558f7de95a4ad3f519e74f9d4c05bb0efdf1aa121659154ebb23d82ebc3aac7",
			golden_hmac, sizeof(golden_hmac));
	accepted = iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg), golden_hmac);
	CHECK(accepted, "device accepts an HMAC computed independently by the Go PC tool");

	replayed = iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg), golden_hmac);
	CHECK(!replayed, "replaying the same (nonce, hmac) a second time is rejected");
}

/* Test 5: a nonce older than IAP_AUTH_NONCE_TTL_MS must be rejected even
 * with a correctly-computed HMAC. */
static void test_nonce_expiry(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	uint8_t nonce_bytes[IAP_AUTH_NONCE_SIZE];
	uint8_t device_key[IAP_DEVICE_KEY_SIZE];
	uint8_t buf[IAP_AUTH_NONCE_SIZE + 64U];
	uint8_t hmac[IAP_AUTH_HMAC_SIZE];
	const char *msg = "openplc_server_reboot";
	bool accepted;

	test_hal_reset();
	test_hal_set_uid(0x01234567U, 0x89ABCDEFU, 0xDEADBEEFU);
	test_hal_set_tick(1000U);

	iap_auth_issue_challenge(nonce_hex);
	hex_decode(nonce_hex, nonce_bytes, sizeof(nonce_bytes));

	test_hal_set_tick(1000U + IAP_AUTH_NONCE_TTL_MS + 1U);

	iap_keyderive_get_device_key(device_key);
	memcpy(buf, nonce_bytes, sizeof(nonce_bytes));
	memcpy(buf + sizeof(nonce_bytes), msg, strlen(msg));
	hmac_sha256(device_key, sizeof(device_key), buf, (uint32_t)(sizeof(nonce_bytes) + strlen(msg)), hmac);

	accepted = iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg), hmac);
	CHECK(!accepted, "a correctly-signed but expired nonce (>30s old) is rejected");
}

/* Test 6: an HMAC computed with a DIFFERENT device's key (i.e. an attacker
 * who only knows the fixed password and this device's public UID from
 * discovery, but is impersonating using another device's derivation) must
 * not authenticate against this device's nonce. */
static void test_wrong_device_key_rejected(void)
{
	char nonce_hex[IAP_AUTH_NONCE_SIZE * 2U + 1U];
	uint8_t nonce_bytes[IAP_AUTH_NONCE_SIZE];
	uint8_t wrong_key[IAP_DEVICE_KEY_SIZE];
	uint8_t buf[IAP_AUTH_NONCE_SIZE + 64U];
	uint8_t forged_hmac[IAP_AUTH_HMAC_SIZE];
	const char *msg = "flash 10 00000000 00";
	bool accepted;

	test_hal_reset();
	test_hal_set_uid(0xAAAAAAAAU, 0xBBBBBBBBU, 0xCCCCCCCCU); /* the real device */
	test_hal_set_tick(10U);
	iap_auth_issue_challenge(nonce_hex);
	hex_decode(nonce_hex, nonce_bytes, sizeof(nonce_bytes));

	test_hal_set_uid(0x11111111U, 0x22222222U, 0x33333333U); /* a different device */
	iap_keyderive_get_device_key(wrong_key);
	test_hal_set_uid(0xAAAAAAAAU, 0xBBBBBBBBU, 0xCCCCCCCCU); /* restore for verify_and_consume */

	memcpy(buf, nonce_bytes, sizeof(nonce_bytes));
	memcpy(buf + sizeof(nonce_bytes), msg, strlen(msg));
	hmac_sha256(wrong_key, sizeof(wrong_key), buf, (uint32_t)(sizeof(nonce_bytes) + strlen(msg)), forged_hmac);

	accepted = iap_auth_verify_and_consume((const uint8_t *)msg, (uint32_t)strlen(msg), forged_hmac);
	CHECK(!accepted, "an HMAC signed with a different device's key is rejected");
}

int main(void)
{
	test_crypto_selftest();
	test_key_derivation_determinism_and_uniqueness();
	test_machine_id_hex_format();
	test_golden_cross_language_vector();
	test_nonce_expiry();
	test_wrong_device_key_rejected();

	printf("\n%s (%d failure(s))\n", g_failures == 0 ? "ALL PASS" : "FAILED", g_failures);
	return g_failures == 0 ? 0 : 1;
}
