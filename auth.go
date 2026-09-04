package main

import (
	"crypto/ecdsa"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"IAPTool/iapcert"
)

// computeNonceSig returns hex(ECDSA-sign(leafKey, sha256(nonce || msg))). The
// device verifies this against the leaf public key named by whatever
// certificate accompanies the same command, so no shared secret is involved.
// nonce is decoded from nonceHex (as returned by "authchallenge" /
// "openplc_server_reboot_challenge"); msg must be the exact command string
// being authorized.
func computeNonceSig(leafKey *ecdsa.PrivateKey, nonceHex string, msg string) (string, error) {
	return iapcert.NonceSig(leafKey, nonceHex, msg)
}

// imageAuth is what a "flash" command needs about the image itself: its
// signature, the version to compare against the device, and enough to check
// up front that the device will actually accept that signature.
type imageAuth struct {
	sigHex      string
	localPubHex string   // public key of the signing key used; empty when the .sig came from elsewhere
	imageHash   [32]byte // SHA-256 of the image, what the signature covers
}

// resolveImageAuth signs the image in memory when a signing key is available
// (--key, local_config.json, or <exe dir>/keys/fw_signing_key.pem), and
// otherwise falls back to a sibling .sig signed earlier or on another
// machine.
func resolveImageAuth(binPath string) (imageAuth, error) {
	var auth imageAuth

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
