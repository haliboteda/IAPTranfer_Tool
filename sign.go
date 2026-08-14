package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Raw r||s signature length for P-256, as stored by the bootloader and fed
// to uECC_verify in IAPServer/fw_verify.c. Also the length of the raw
// X||Y public key the bootloader reports for "getpubkey".
const sigLen = 64

// Where the tool looks for a signing key when none was given explicitly:
// <directory holding the executable>/keys/fw_signing_key.pem. Keeping it
// next to the binary means the Arduino IDE needs no path passed in.
const keysDirName = "keys"
const defaultKeyName = "fw_signing_key.pem"

// findSigningKey returns the signing key to use: an explicit --key or
// local_config.json "signing_key" wins, otherwise the default file under
// <exe dir>/keys if it exists. Returns "" when there is no key available.
func findSigningKey() string {
	if g_signing.keyPath != "" {
		return g_signing.keyPath
	}

	exeDir := GetCurDir()
	if exeDir == "" {
		return ""
	}
	candidate := filepath.Join(exeDir, keysDirName, defaultKeyName)
	if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
		return candidate
	}
	return ""
}

// defaultKeyLocation is the path findSigningKey looks for, used in error
// messages so the operator knows where to put a key.
func defaultKeyLocation() string {
	return filepath.Join(GetCurDir(), keysDirName, defaultKeyName)
}

// loadSigningKey reads a PEM-encoded ECDSA P-256 private key, accepting both
// the SEC1 "EC PRIVATE KEY" form written by `openssl ecparam -genkey` and the
// PKCS#8 "PRIVATE KEY" form.
func loadSigningKey(path string) (*ecdsa.PrivateKey, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read signing key %s: %w", path, err)
	}

	block, _ := pem.Decode(data)
	if block == nil {
		return nil, fmt.Errorf("signing key %s is not PEM-encoded", path)
	}

	key, sec1Err := x509.ParseECPrivateKey(block.Bytes)
	if sec1Err != nil {
		parsed, pkcs8Err := x509.ParsePKCS8PrivateKey(block.Bytes)
		if pkcs8Err != nil {
			return nil, fmt.Errorf("failed to parse EC private key %s: %w", path, sec1Err)
		}
		ecKey, ok := parsed.(*ecdsa.PrivateKey)
		if !ok {
			return nil, fmt.Errorf("signing key %s is not an ECDSA key", path)
		}
		key = ecKey
	}

	if key.Curve != elliptic.P256() {
		return nil, fmt.Errorf("signing key %s uses curve %s, the bootloader requires P-256",
			path, key.Curve.Params().Name)
	}
	return key, nil
}

// signImage returns SHA-256(image) and the raw 64-byte r||s signature over
// that hash, each half left-padded to 32 bytes.
func signImage(image []byte, key *ecdsa.PrivateKey) (hash [32]byte, sig []byte, err error) {
	hash = sha256.Sum256(image)
	r, s, err := ecdsa.Sign(rand.Reader, key, hash[:])
	if err != nil {
		return hash, nil, fmt.Errorf("failed to sign image: %w", err)
	}

	sig = make([]byte, sigLen)
	r.FillBytes(sig[:sigLen/2])
	s.FillBytes(sig[sigLen/2:])
	return hash, sig, nil
}

// signBinFile writes the sibling .sha256, .size and .sig files next to the
// image (plus .version when one is given), the same set that
// an earlier "IAPTool sign" run produces.
func signBinFile(binPath, keyPath, outPrefix string, version uint32, haveVersion bool) error {
	key, err := loadSigningKey(keyPath)
	if err != nil {
		return err
	}

	image, err := os.ReadFile(binPath)
	if err != nil {
		return fmt.Errorf("failed to read image %s: %w", binPath, err)
	}

	hash, sig, err := signImage(image, key)
	if err != nil {
		return err
	}

	if outPrefix == "" {
		outPrefix = strings.TrimSuffix(binPath, ".bin")
	}

	written := []string{outPrefix + ".sha256", outPrefix + ".size", outPrefix + ".sig"}
	if err := os.WriteFile(outPrefix+".sha256", []byte(hex.EncodeToString(hash[:])+"\n"), 0644); err != nil {
		return fmt.Errorf("failed to write %s.sha256: %w", outPrefix, err)
	}
	if err := os.WriteFile(outPrefix+".size", []byte(strconv.Itoa(len(image))+"\n"), 0644); err != nil {
		return fmt.Errorf("failed to write %s.size: %w", outPrefix, err)
	}
	if err := os.WriteFile(outPrefix+".sig", sig, 0644); err != nil {
		return fmt.Errorf("failed to write %s.sig: %w", outPrefix, err)
	}

	if haveVersion {
		if err := os.WriteFile(outPrefix+".version", []byte(strconv.FormatUint(uint64(version), 10)+"\n"), 0644); err != nil {
			return fmt.Errorf("failed to write %s.version: %w", outPrefix, err)
		}
		written = append(written, outPrefix+".version")
		logf("Wrote %s for %s", strings.Join(written, " "), binPath)
		logf("sha256=%s size=%d version=%d", hex.EncodeToString(hash[:]), len(image), version)
		return nil
	}

	logf("Wrote %s for %s", strings.Join(written, " "), binPath)
	logf("sha256=%s size=%d", hex.EncodeToString(hash[:]), len(image))
	return nil
}

// generateSigningKey writes a new P-256 private key as <name>.pem and prints
// the matching keys/fw_pubkey.inc body on stdout, so a caller can redirect it
// straight into place. Progress messages go to stderr to keep stdout clean.
func generateSigningKey(name string) error {
	privPath := name + ".pem"

	if _, err := os.Stat(privPath); err == nil {
		return fmt.Errorf("refusing to overwrite existing %s", privPath)
	}

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return fmt.Errorf("failed to generate keypair: %w", err)
	}

	privDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return fmt.Errorf("failed to encode private key: %w", err)
	}
	privPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: privDER})
	if err := os.WriteFile(privPath, privPEM, 0600); err != nil {
		return fmt.Errorf("failed to write %s: %w", privPath, err)
	}

	fmt.Fprintf(os.Stderr, "Private key written to: %s  -- KEEP THIS OFFLINE, DO NOT COMMIT IT\n", privPath)
	fmt.Print(formatPubKeyInc(&key.PublicKey))
	return nil
}

// rawPublicKey returns the uncompressed point as X||Y, the form the
// bootloader stores in fw_public_key[64].
func rawPublicKey(pub *ecdsa.PublicKey) []byte {
	raw := make([]byte, sigLen)
	pub.X.FillBytes(raw[:sigLen/2])
	pub.Y.FillBytes(raw[sigLen/2:])
	return raw
}

// publicKeyHex is rawPublicKey hex-encoded, directly comparable with what
// the device answers to "getpubkey".
func publicKeyHex(pub *ecdsa.PublicKey) string {
	return hex.EncodeToString(rawPublicKey(pub))
}

// verifySignatureWithPubKey checks a raw r||s signature against a raw
// 64-byte X||Y public key -- the same pair the bootloader hands to
// uECC_verify, so a pass here means the device will accept the image.
func verifySignatureWithPubKey(hash [32]byte, sig []byte, pubRaw []byte) bool {
	if len(sig) != sigLen || len(pubRaw) != sigLen {
		return false
	}
	pub := &ecdsa.PublicKey{
		Curve: elliptic.P256(),
		X:     new(big.Int).SetBytes(pubRaw[:sigLen/2]),
		Y:     new(big.Int).SetBytes(pubRaw[sigLen/2:]),
	}
	r := new(big.Int).SetBytes(sig[:sigLen/2])
	s := new(big.Int).SetBytes(sig[sigLen/2:])
	return ecdsa.Verify(pub, hash[:], r, s)
}

// formatPubKeyInc renders the uncompressed point (X||Y) as the body of
// IAPServer/keys/fw_pubkey.inc, which fw_pubkey.c #includes between braces.
func formatPubKeyInc(pub *ecdsa.PublicKey) string {
	raw := rawPublicKey(pub)

	var b strings.Builder
	b.WriteString("/* Firmware signing public key, secp256r1 uncompressed point (X||Y).\n")
	b.WriteString(" * Generated by IAPTool genkey -- do not hand-edit. */\n")
	for i, v := range raw {
		b.WriteString(fmt.Sprintf("0x%02x", v))
		if i != len(raw)-1 {
			b.WriteString(",")
		}
		if i%16 == 15 {
			b.WriteString("\n")
		} else {
			b.WriteString(" ")
		}
	}
	return b.String()
}
