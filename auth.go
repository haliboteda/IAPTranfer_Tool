package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"IAPTool/iapcrypto"
)

// deriveDeviceKeyFromUIDHex decodes a device's UID (as reported by "getuid"
// or the UDP discovery/ping reply) and derives that device's own auth key.
func deriveDeviceKeyFromUIDHex(uidHex string) ([]byte, error) {
	machineID, err := hex.DecodeString(strings.TrimSpace(uidHex))
	if err != nil {
		return nil, fmt.Errorf("invalid device UID hex %q: %w", uidHex, err)
	}
	return iapcrypto.DeriveDeviceKey(machineID), nil
}

// computeAuthHMAC returns hex(HMAC-SHA256(deviceKey, nonce || msg)), where
// nonce is decoded from nonceHex (as returned by "authchallenge" /
// "openplc_server_reboot_challenge"). msg must be the exact command string
// being authorized -- the device recomputes the same construction using its
// own copy of deviceKey (derived from its own UID).
func computeAuthHMAC(deviceKey []byte, nonceHex string, msg string) (string, error) {
	nonce, err := hex.DecodeString(strings.TrimSpace(nonceHex))
	if err != nil {
		return "", fmt.Errorf("invalid nonce hex %q: %w", nonceHex, err)
	}
	data := append(append([]byte{}, nonce...), []byte(msg)...)
	return hex.EncodeToString(iapcrypto.HMACSHA256(deviceKey, data)), nil
}

// imageAuth is what a "flash" command needs about the image itself: its
// signature, the version to compare against the device, and enough to check
// up front that the device will actually accept that signature.
type imageAuth struct {
	sigHex      string
	version     uint32
	haveVersion bool
	localPubHex string   // public key of the signing key used; empty when the .sig came from elsewhere
	imageHash   [32]byte // SHA-256 of the image, what the signature covers
}

// resolveImageAuth signs the image in memory when a signing key is available
// (--key, local_config.json, or <exe dir>/keys/fw_signing_key.pem), and
// otherwise falls back to a sibling .sig signed earlier or on another
// machine. The version comes from --version when given, else from a sibling
// .version file.
func resolveImageAuth(binPath string) (imageAuth, error) {
	auth := imageAuth{version: g_signing.version, haveVersion: g_signing.haveVersion}

	if !auth.haveVersion {
		version, ok, err := loadVersion(binPath)
		if err != nil {
			return auth, err
		}
		auth.version, auth.haveVersion = version, ok
	}
	if !auth.haveVersion {
		// Say it out loud. Without a version there is nothing to compare the
		// device against, so the downgrade check does not run at all -- and a
		// protection that silently does not run is worse than none.
		logf("WARNING: no version for this image (no --version and no sibling .version file). " +
			"The downgrade check will NOT run.")
	}

	image, err := os.ReadFile(binPath)
	if err != nil {
		return auth, fmt.Errorf("failed to read image %s: %w", binPath, err)
	}
	auth.imageHash = sha256.Sum256(image)

	keyPath := findSigningKey()
	if keyPath == "" {
		sigHex, sigErr := loadSignature(binPath)
		if sigErr == nil {
			auth.sigHex = sigHex
			return auth, nil
		}
		if !errors.Is(sigErr, os.ErrNotExist) {
			return auth, sigErr
		}
		return auth, fmt.Errorf("no signing key found and no signature next to %s.\n"+
			"  Put a signing key at %s,\n"+
			"  or pass --key=<key.pem>,\n"+
			"  or place a %s.sig signed on another machine",
			filepath.Base(binPath), defaultKeyLocation(), strings.TrimSuffix(binPath, ".bin"))
	}

	key, err := loadSigningKey(keyPath)
	if err != nil {
		return auth, err
	}
	_, sig, err := signImage(image, key)
	if err != nil {
		return auth, err
	}
	auth.sigHex = hex.EncodeToString(sig)
	auth.localPubHex = publicKeyHex(&key.PublicKey)
	logf("Signed %s in memory using %s", filepath.Base(binPath), keyPath)
	return auth, nil
}

// pubKeyQuery asks the device which signing key it verifies against. Each
// transport supplies its own (CDC serial, or the ethernet TCP connection).
type pubKeyQuery func() (string, error)

// verifyKeyMatchesDevice confirms up front that this device will accept the
// image's signature, so a key mismatch is reported before spending time on
// the transfer instead of after it.
//
// A bootloader too old to know "getpubkey", or one that cannot be reached,
// only produces a warning: the flash that follows fails on its own if
// something is genuinely wrong, and refusing here would break boards that
// worked before this check existed.
func verifyKeyMatchesDevice(auth imageAuth, askDevice pubKeyQuery) error {
	reply, err := askDevice()
	if err != nil {
		logf("Could not read the device public key (%v) -- skipping key match check", err)
		return nil
	}

	devicePubHex := strings.ToLower(strings.TrimSpace(reply))
	devicePub, decodeErr := hex.DecodeString(devicePubHex)
	if decodeErr != nil || len(devicePub) != sigLen {
		logf("This bootloader does not support %q (replied %q) -- skipping key match check",
			CM_GetPubKey, strings.TrimSpace(reply))
		return nil
	}

	if auth.localPubHex != "" {
		localPubHex := strings.ToLower(auth.localPubHex)
		if devicePubHex == localPubHex {
			logf("Signing key matches this board (%s...)", devicePubHex[:16])
			return nil
		}
		return fmt.Errorf("this board's bootloader verifies against a different signing key.\n"+
			"  board: %s...\n"+
			"  local: %s...\n"+
			"  Use the private key that matches this board, or flash a bootloader built from the local key",
			devicePubHex[:16], localPubHex[:16])
	}

	sig, decodeErr := hex.DecodeString(auth.sigHex)
	if decodeErr != nil {
		return fmt.Errorf("invalid signature hex: %w", decodeErr)
	}
	if !verifySignatureWithPubKey(auth.imageHash, sig, devicePub) {
		return fmt.Errorf("the .sig does not verify against this board's public key (%s...).\n"+
			"  The image was signed with a different key, or it changed after being signed",
			devicePubHex[:16])
	}
	logf("Signature verifies against this board's public key (%s...)", devicePubHex[:16])
	return nil
}

// loadSignature reads the raw 64-byte ECDSA signature produced by
// "IAPTool sign" for the given .bin (expects a
// sibling <name>.sig file) and returns it hex-encoded.
func loadSignature(binPath string) (string, error) {
	sigPath := strings.TrimSuffix(binPath, ".bin") + ".sig"
	data, err := os.ReadFile(sigPath)
	if err != nil {
		return "", fmt.Errorf("failed to read signature file %s (run \"IAPTool sign\" on this .bin first): %w", sigPath, err)
	}
	if len(data) != 64 {
		return "", fmt.Errorf("signature file %s has %d bytes, expected 64", sigPath, len(data))
	}
	return hex.EncodeToString(data), nil
}

// loadVersion reads an optional sibling <name>.version file (decimal ASCII,
// written by "IAPTool sign --version=N") next to the .bin. Returns
// ok=false with no error if the file simply doesn't exist -- version
// tracking is opt-in; an image signed without one still flashes exactly as
// before this feature existed, just with no downgrade comparison possible.
func loadVersion(binPath string) (version uint32, ok bool, err error) {
	verPath := strings.TrimSuffix(binPath, ".bin") + ".version"
	data, readErr := os.ReadFile(verPath)
	if readErr != nil {
		if os.IsNotExist(readErr) {
			return 0, false, nil
		}
		return 0, false, fmt.Errorf("failed to read version file %s: %w", verPath, readErr)
	}
	v, parseErr := strconv.ParseUint(strings.TrimSpace(string(data)), 10, 32)
	if parseErr != nil {
		return 0, false, fmt.Errorf("invalid version in %s: %w", verPath, parseErr)
	}
	return uint32(v), true, nil
}

// encodeSemver turns a dotted version ("0.1.3", "0.1.3.2") into the uint32 the
// device compares, one byte per field, most significant first. Any trailing
// non-numeric text is ignored, so "0.1.3-pre" encodes exactly like "0.1.3": use
// the fourth field when a pre-release has to sort below its release.
func encodeSemver(s string) (uint32, error) {
	fields := strings.SplitN(strings.TrimSpace(s), ".", 4)
	var encoded uint32

	for i := 0; i < 4; i++ {
		var digits string
		if i < len(fields) {
			digits = strings.TrimLeft(fields[i], " ")
			cut := strings.IndexFunc(digits, func(r rune) bool { return r < '0' || r > '9' })
			if cut >= 0 {
				digits = digits[:cut]
			}
		}
		if digits == "" {
			if i == 0 {
				return 0, fmt.Errorf("version %q does not start with a number", s)
			}
			digits = "0"
		}
		value, err := strconv.ParseUint(digits, 10, 32)
		if err != nil || value > 255 {
			return 0, fmt.Errorf("version %q: field %d must be 0-255", s, i+1)
		}
		encoded |= uint32(value) << uint(8*(3-i))
	}

	return encoded, nil
}

// confirmDowngradeIfNeeded compares the version about to be flashed against
// what the device reports installed (via "getversion"), and if it's a
// downgrade, warns and requires an explicit "yes" in this console before
// proceeding. The device itself never blocks a downgrade -- this operator
// confirmation is the only gate, and it's skipped entirely (proceeds) if
// either side has no version to compare: no local .version file, or a
// device reply that doesn't parse as a number (older bootloader without
// "getversion" support replies "Unknown command", for example).
func confirmDowngradeIfNeeded(localVersion uint32, remoteVerStr string) bool {
	remoteVersion, err := strconv.ParseUint(strings.TrimSpace(remoteVerStr), 10, 32)
	if err != nil {
		logf("Could not parse device-reported version %q, skipping downgrade check", remoteVerStr)
		return true
	}
	if localVersion >= uint32(remoteVersion) {
		return true
	}

	logf("WARNING: device currently has version %d installed; this image is version %d (older).",
		remoteVersion, localVersion)

	switch g_signing.downgrade {
	case DowngradeAllow:
		logf("Downgrade allowed by --downgrade=allow.")
		return true
	case DowngradeRefuse:
		logf("Downgrade refused by --downgrade=refuse.")
		return false
	}

	// "ask" needs somebody to ask. Started from an IDE there is no console to
	// type into, and reading stdin there returns EOF straight away -- which used
	// to be reported as "declined by operator" even though nobody was asked.
	if !stdinIsInteractive() {
		logf("Cannot ask: no interactive terminal (started from an IDE?). Refusing the downgrade. " +
			"Choose the downgrade option in the board menu, or pass --downgrade=allow.")
		return false
	}

	fmt.Print("Proceed with this downgrade? [y/N]: ")
	answer, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	answer = strings.ToLower(strings.TrimSpace(answer))
	return answer == "y" || answer == "yes"
}

// stdinIsInteractive reports whether stdin is a console the operator can type
// into, as opposed to a pipe handed over by a build tool.
func stdinIsInteractive() bool {
	info, err := os.Stdin.Stat()
	return err == nil && (info.Mode()&os.ModeCharDevice) != 0
}
