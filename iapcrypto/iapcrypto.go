// Package iapcrypto derives the per-device authentication key shared with
// the bootloader/app HMAC challenge-response protocol (see
// open_plc_cube_ide/IAPServer/iap_keyderive.c and its Arduino-core mirror in
// libraries/OpenPLC_IAP/src/iap_keyderive.c -- all three copies must compute
// the same key from the same inputs).
//
// The current scheme is intentionally isolated here: DeriveDeviceKey is the
// only place the fixed-password+machine-ID construction is expressed, so
// swapping it for a provisioned per-device secret later touches this file
// only, not any caller.
package iapcrypto

import (
	"crypto/hmac"
	"crypto/sha256"
)

// FixedPassword is the shared master password mixed with each device's
// machine ID (its STM32 96-bit UID) to derive that device's own key.
//
// *** PLACEHOLDER TEST-ONLY VALUE, defined in fixed_password_generated.go ***
// Single source of truth is open_plc_cube_ide/IAPServer/iap_fixed_password.txt
// -- edit that file and run generate_fixed_password.sh/.ps1 there to
// regenerate this copy together with both C copies. Replace with a
// provisioned secret before production use; see IAPServer/keys/README.md.

// DeriveDeviceKey returns this machine's 32-byte device key:
// HMAC-SHA256(FixedPassword, machineID). machineID is the raw (non-hex)
// STM32 UID bytes, as decoded from the hex string reported by "getuid" /
// the UDP discovery reply.
func DeriveDeviceKey(machineID []byte) []byte {
	return HMACSHA256([]byte(FixedPassword), machineID)
}

// HMACSHA256 is the single HMAC primitive used both to derive a device key
// and to sign challenge-response messages with it, so the whole protocol's
// crypto primitive is swappable in one place.
func HMACSHA256(key, data []byte) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write(data)
	return mac.Sum(nil)
}
