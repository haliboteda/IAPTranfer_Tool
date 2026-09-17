package main

import (
	"bytes"
	"crypto/ecdsa"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"IAPTool/iapcert"
)

// uploadIdentity is what this machine presents to a board: the private key it
// signs with, and the certificate saying a root the board trusts authorised
// that key.
//
// Two ways to come by one, and the board cannot tell them apart -- which is
// the point (see $PROD/docs/modules/M2-ownership.md, "客户自己建根之后，他的密钥怎么和根挂上"):
//
//   - No certificate file: the key certifies itself. One person, one key, the
//     key is the root. This is what a fresh install does.
//   - A certificate file: an administrator holding the root issued it for this
//     key. The root private key never has to be on this machine.
type uploadIdentity struct {
	key       *ecdsa.PrivateKey
	cert      *iapcert.Cert
	certHex   string
	delegated bool // the certificate came from a file rather than self-signing
}

// resolveUploadIdentity loads the signing key and the certificate that goes
// with it. Session authentication signs a fresh challenge, so the private key
// itself is required -- there is no offline-signature path for this step.
func resolveUploadIdentity() (uploadIdentity, error) {
	var id uploadIdentity

	keyPath := findSigningKey()
	if keyPath == "" {
		return id, fmt.Errorf("no signing key found at %s.\n"+
			"  Authenticating an upload means signing a challenge, which needs the private key.\n"+
			"  Put one there, or pass --key=<key.pem>", defaultKeyLocation())
	}
	key, err := loadSigningKey(keyPath)
	if err != nil {
		return id, err
	}
	id.key = key

	certPath := findCert(keyPath)
	if certPath == "" {
		certHex, issueErr := issueLeafCert(keyPath, "")
		if issueErr != nil {
			return id, fmt.Errorf("cannot issue a certificate for %s: %w", keyPath, issueErr)
		}
		id.certHex = certHex
	} else {
		data, readErr := os.ReadFile(certPath)
		if readErr != nil {
			return id, fmt.Errorf("failed to read certificate %s: %w", certPath, readErr)
		}
		id.certHex = strings.TrimSpace(string(data))
		id.delegated = true
	}

	id.cert, err = iapcert.ParseHex(id.certHex)
	if err != nil {
		return id, fmt.Errorf("%s: %w", certPath, err)
	}

	// A certificate issued for somebody else's key is useless here: the board
	// verifies the image and the challenge against the leaf key the
	// certificate names, and this machine can only sign with its own.
	localPub := iapcert.RawPublicKey(&key.PublicKey)
	if !bytes.Equal(id.cert.LeafPub, localPub) {
		return id, fmt.Errorf("the certificate %s was issued for a different key.\n"+
			"  certificate names: %s...\n"+
			"  %s is:             %s...\n"+
			"  Ask for a certificate covering this key, or point --key at the one it covers",
			certPath, hex.EncodeToString(id.cert.LeafPub)[:16],
			filepath.Base(keyPath), hex.EncodeToString(localPub)[:16])
	}

	if id.delegated {
		logf("Using certificate %s (serial %d) for %s", certPath, id.cert.Serial, keyPath)
	}
	return id, nil
}

// signImageInMemory signs the image at binPath and returns the raw r||s
// signature, hex-encoded. Nothing is written to disk.
func signImageInMemory(binPath string, key *ecdsa.PrivateKey) (string, error) {
	image, err := os.ReadFile(binPath)
	if err != nil {
		return "", fmt.Errorf("failed to read image %s: %w", binPath, err)
	}
	_, sig, err := signImage(image, key)
	if err != nil {
		return "", err
	}
	logf("Signed %s in memory", filepath.Base(binPath))
	return hex.EncodeToString(sig), nil
}

// pubKeyQuery asks the device which root it verifies against. Each transport
// supplies its own (CDC serial, or the ethernet TCP connection).
type pubKeyQuery func() (string, error)

// verifyIdentityMatchesDevice confirms up front that this board will accept
// what we are about to send, so a mismatch is reported before spending time on
// the transfer instead of after it.
//
// The question is the same one the board asks: does the root this board trusts
// vouch for our certificate. For a self-signed certificate that reduces to
// "is our key the board's root", which is why both cases are one check.
//
// A bootloader too old to know "getpubkey", or one that cannot be reached,
// only produces a warning: the flash that follows fails on its own if
// something is genuinely wrong, and refusing here would break boards that
// worked before this check existed.
func verifyIdentityMatchesDevice(id uploadIdentity, askDevice pubKeyQuery) error {
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

	if id.cert.VerifiedBy(devicePub) {
		if id.delegated {
			logf("Certificate was issued by this board's root (%s...)", devicePubHex[:16])
		} else {
			logf("Signing key matches this board (%s...)", devicePubHex[:16])
		}
		return nil
	}

	if id.delegated {
		return fmt.Errorf("this certificate was not issued by this board's root.\n"+
			"  board trusts: %s...\n"+
			"  Ask the holder of that root for a certificate, or point this board's owner at the right key",
			devicePubHex[:16])
	}
	return fmt.Errorf("this board's bootloader verifies against a different signing key.\n"+
		"  board: %s...\n"+
		"  local: %s...\n"+
		"  Use the private key that matches this board, or flash a bootloader built from the local key",
		devicePubHex[:16], hex.EncodeToString(id.cert.LeafPub)[:16])
}
