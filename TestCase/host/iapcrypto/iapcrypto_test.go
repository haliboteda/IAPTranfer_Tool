// Package testcase holds standalone Go tests for the per-device key
// derivation used by the IAP challenge-response protocol (see
// IAPTranfer_Tool/iapcrypto). Run with: go test ./testcase/...
package testcase

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"testing"

	"IAPTool/iapcrypto"
)

// TestHMACSHA256_RFC4231Vector pins iapcrypto's HMAC primitive against the
// standard RFC 4231 test case 1, so swapping the underlying implementation
// later can't silently produce a non-standard HMAC-SHA256.
func TestHMACSHA256_RFC4231Vector(t *testing.T) {
	key := bytes.Repeat([]byte{0x0b}, 20)
	data := []byte("Hi There")
	want := "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"

	got := hex.EncodeToString(iapcrypto.HMACSHA256(key, data))
	if got != want {
		t.Fatalf("HMACSHA256 mismatch: got %s, want %s", got, want)
	}
}

// TestDeriveDeviceKey_MatchesIndependentConstruction locks in the exact
// derivation formula -- HMAC-SHA256(password, machineID) -- by recomputing it
// independently of iapcrypto's own HMACSHA256 helper. If the key/message
// arguments are ever swapped, this catches it.
//
// The password is installed explicitly rather than read from
// keys/iap_fixed_password.txt: what this pins down is the construction, not
// whichever value happens to be in the key file, so rotating the real password
// must not turn this test red.
func TestDeriveDeviceKey_MatchesIndependentConstruction(t *testing.T) {
	const pw = "test-only password, not the shipped one"
	iapcrypto.SetFixedPassword([]byte(pw))

	machineID, _ := hex.DecodeString("0011223344556677889900aa")

	mac := hmac.New(sha256.New, []byte(pw))
	mac.Write(machineID)
	want := mac.Sum(nil)

	got := iapcrypto.DeriveDeviceKey(machineID)
	if !bytes.Equal(got, want) {
		t.Fatalf("DeriveDeviceKey mismatch: got %x, want %x", got, want)
	}
}

// TestDeriveDeviceKey_Deterministic ensures the same machine ID always
// derives the same key -- required so a device's key is stable across boots
// and so the PC tool can recompute it after only learning the UID.
func TestDeriveDeviceKey_Deterministic(t *testing.T) {
	machineID, _ := hex.DecodeString("00112233445566778899aabb")

	k1 := iapcrypto.DeriveDeviceKey(machineID)
	k2 := iapcrypto.DeriveDeviceKey(machineID)
	if !bytes.Equal(k1, k2) {
		t.Fatalf("DeriveDeviceKey is not deterministic: %x != %x", k1, k2)
	}
	if len(k1) != sha256.Size {
		t.Fatalf("DeriveDeviceKey returned %d bytes, want %d", len(k1), sha256.Size)
	}
}

// TestDeriveDeviceKey_PerDeviceUniqueness is the actual security property
// the whole feature exists for: two different machine IDs (i.e. two
// different physical boards) must never derive the same device key.
func TestDeriveDeviceKey_PerDeviceUniqueness(t *testing.T) {
	uidsHex := []string{
		"00112233445566778899aabb",
		"00112233445566778899aabc", // differs in the last nibble only
		"ffffffffffffffffffffffff",
		"000000000000000000000000",
	}

	seen := make(map[string]string)
	for _, uidHex := range uidsHex {
		machineID, err := hex.DecodeString(uidHex)
		if err != nil {
			t.Fatalf("bad test UID %q: %v", uidHex, err)
		}
		key := hex.EncodeToString(iapcrypto.DeriveDeviceKey(machineID))
		if prevUID, dup := seen[key]; dup {
			t.Fatalf("UID %q and %q derived the same device key %s", uidHex, prevUID, key)
		}
		seen[key] = uidHex
	}
}

// TestChallengeResponse_DeviceAndToolAgree simulates one full authchallenge
// round trip: the "device" issues a nonce and later verifies an HMAC; the
// "PC tool" computes that HMAC using only the device's UID and the shared
// fixed password. Both sides must derive the identical device key and
// identical HMAC independently, exactly as they would over a real serial/
// network link.
func TestChallengeResponse_DeviceAndToolAgree(t *testing.T) {
	deviceUIDHex := "aabbccddeeff001122334455"
	nonce := []byte{0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
		0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10}
	command := "flash 1024 deadbeef " + "ab"

	machineID, err := hex.DecodeString(deviceUIDHex)
	if err != nil {
		t.Fatalf("bad UID: %v", err)
	}

	// "device side": derives its key from its own UID.
	deviceKey := iapcrypto.DeriveDeviceKey(machineID)
	deviceHMAC := iapcrypto.HMACSHA256(deviceKey, append(append([]byte{}, nonce...), command...))

	// "PC tool side": derives the same key from the UID the device reported
	// (e.g. via "getuid" or the discovery reply), independently of the
	// device-side computation above.
	toolKey := iapcrypto.DeriveDeviceKey(machineID)
	toolHMAC := iapcrypto.HMACSHA256(toolKey, append(append([]byte{}, nonce...), command...))

	if !bytes.Equal(deviceHMAC, toolHMAC) {
		t.Fatalf("device and tool disagree on HMAC: device=%x tool=%x", deviceHMAC, toolHMAC)
	}

	// A different (wrong) UID must not authenticate this command.
	wrongMachineID, _ := hex.DecodeString("000000000000000000000000")
	wrongKey := iapcrypto.DeriveDeviceKey(wrongMachineID)
	wrongHMAC := iapcrypto.HMACSHA256(wrongKey, append(append([]byte{}, nonce...), command...))
	if bytes.Equal(deviceHMAC, wrongHMAC) {
		t.Fatalf("wrong UID produced a matching HMAC -- per-device keying is broken")
	}
}
