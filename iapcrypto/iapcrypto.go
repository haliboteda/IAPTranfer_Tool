// Package iapcrypto derives the per-device authentication key shared with
// the bootloader/app HMAC challenge-response protocol (see
// open_plc_cube_ide/IAPServer/iap_keyderive.c and its Arduino-core mirror in
// libraries/OpenPLC_IAP/src/iap_keyderive.c -- all three copies must compute
// the same key from the same inputs).
//
// The scheme is isolated here: DeriveDeviceKey is the only place the
// fixed-password+machine-ID construction is expressed.
package iapcrypto

import (
	"crypto/hmac"
	"crypto/sha256"
)

// fixedPassword is the shared master password mixed with each device's
// machine ID to derive that device's own key. The C side compiles it in from
// keys/iap_fixed_password.txt; this side loads the same file at run time, so
// rotating the password needs no rebuild of this tool.
var fixedPassword []byte

// SetFixedPassword installs the password read from keys/iap_fixed_password.txt.
func SetFixedPassword(pw []byte) {
	fixedPassword = append([]byte(nil), pw...)
}

// DeriveDeviceKey returns this machine's 32-byte device key:
// HMAC-SHA256(fixedPassword, machineID). machineID is the raw (non-hex)
// STM32 UID bytes, as decoded from the hex string reported by "getuid" /
// the UDP discovery reply.
func DeriveDeviceKey(machineID []byte) []byte {
	return HMACSHA256(fixedPassword, machineID)
}

// HMACSHA256 is the single HMAC primitive used both to derive a device key
// and to sign challenge-response messages with it, so the whole protocol's
// crypto primitive is swappable in one place.
func HMACSHA256(key, data []byte) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write(data)
	return mac.Sum(nil)
}
