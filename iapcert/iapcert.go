// Package iapcert holds the certificate and challenge-signing primitives the
// IAP protocol runs on. It is a package rather than part of IAPTool's main so
// that the test cases can import it: a case that reimplements the crypto
// proves only that the reimplementation agrees with itself.
//
// The wire format mirrors iap_cert_t in
// open_plc_cube_ide/IAPServer/iap_cert.h byte-for-byte:
//
//	leaf_pubkey[64] || serial(uint32 LE) || root_sig[64]   (132 bytes)
//
// Simple mode (LeafPubHex == "") self-signs: the certificate says "this
// root's own key is authorised", and the board's iap_cert_verify() has no
// branch for that -- it is a certificate like any other, just one whose leaf
// happens to equal its root. See $PROD/docs/security/OWNERSHIP.md for why having no
// special case is the whole point.
package iapcert

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"strconv"
	"strings"
)

const (
	// SigLen is a raw secp256r1 signature, r||s.
	SigLen = 64
	// Size is one whole certificate on the wire.
	Size = 132
	// SignedLen is the prefix the root signature covers: leaf_pubkey||serial.
	SignedLen = 68

	// SelfSignedSerial is the serial every self-signed certificate carries.
	//
	// Serial numbers exist to be named by a revocation, and a root cannot
	// revoke itself into uselessness -- so the root's own certificate is
	// deliberately kept out of the numbered range instead of drawing from it.
	// Two things follow, and both matter more than they look:
	//
	//   - Revoking a delegated leaf can never take the root's own uploads with
	//     it. A shared counter would interleave them, and revoking a colleague
	//     issued on Monday would invalidate firmware the administrator uploaded
	//     on Friday.
	//   - Uploading stops touching the counter file at all. It lives beside the
	//     private key, which for an Arduino install means inside the board
	//     package -- a directory nothing else writes to during an upload.
	SelfSignedSerial = 0xFFFFFFFF
)

// Cert is a parsed certificate. Three fields at fixed offsets and nothing
// else, the same shape iap_cert.h parses -- Raw is kept because the root
// signature covers the bytes, not the fields.
type Cert struct {
	LeafPub []byte // 64, secp256r1 X||Y
	Serial  uint32
	RootSig []byte // 64, over sha256(Raw[:SignedLen])
	Raw     []byte // Size bytes
}

// ParseHex decodes the 264-hex-character form that goes on the wire.
func ParseHex(certHex string) (*Cert, error) {
	raw, err := hex.DecodeString(strings.TrimSpace(certHex))
	if err != nil {
		return nil, fmt.Errorf("certificate is not hex: %w", err)
	}
	if len(raw) != Size {
		return nil, fmt.Errorf("certificate is %d bytes, expected %d", len(raw), Size)
	}
	return &Cert{
		LeafPub: raw[:64],
		Serial:  binary.LittleEndian.Uint32(raw[64:68]),
		RootSig: raw[68:Size],
		Raw:     raw,
	}, nil
}

// VerifiedBy answers the one question the board asks of a certificate: did
// this root sign it. Mirrors iap_cert_verify() in
// open_plc_cube_ide/IAPServer/iap_cert.c, so the tool can refuse a
// certificate the board would refuse -- before spending a transfer on it.
//
// Says nothing about the image or the challenge that certificate will go on
// to authorise; those are separate checks, on the board.
func (c *Cert) VerifiedBy(rootPub []byte) bool {
	if len(rootPub) != SigLen {
		return false
	}
	pub := &ecdsa.PublicKey{
		Curve: elliptic.P256(),
		X:     new(big.Int).SetBytes(rootPub[:32]),
		Y:     new(big.Int).SetBytes(rootPub[32:]),
	}
	if !pub.Curve.IsOnCurve(pub.X, pub.Y) {
		return false
	}
	digest := sha256.Sum256(c.Raw[:SignedLen])
	r := new(big.Int).SetBytes(c.RootSig[:32])
	s := new(big.Int).SetBytes(c.RootSig[32:])
	return ecdsa.Verify(pub, digest[:], r, s)
}

// LoadKey reads a PEM-encoded ECDSA P-256 private key, accepting both the
// SEC1 "EC PRIVATE KEY" form written by `openssl ecparam -genkey` and the
// PKCS#8 "PRIVATE KEY" form.
func LoadKey(path string) (*ecdsa.PrivateKey, error) {
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
			return nil, fmt.Errorf("%s holds a %T, not an EC private key", path, parsed)
		}
		key = ecKey
	}
	return key, nil
}

// RawPublicKey is the uncompressed point X||Y, with no 0x04 prefix -- the
// form the board stores and compares.
func RawPublicKey(pub *ecdsa.PublicKey) []byte {
	raw := make([]byte, SigLen)
	pub.X.FillBytes(raw[:SigLen/2])
	pub.Y.FillBytes(raw[SigLen/2:])
	return raw
}

// PublicKeyHex is RawPublicKey hex-encoded, directly comparable with what the
// device answers to "getpubkey".
func PublicKeyHex(pub *ecdsa.PublicKey) string {
	return hex.EncodeToString(RawPublicKey(pub))
}

// SignDigest signs a 32-byte SHA-256 digest, returning raw r||s.
func SignDigest(key *ecdsa.PrivateKey, digest []byte) ([]byte, error) {
	r, s, err := ecdsa.Sign(rand.Reader, key, digest)
	if err != nil {
		return nil, err
	}
	sig := make([]byte, SigLen)
	r.FillBytes(sig[:SigLen/2])
	s.FillBytes(sig[SigLen/2:])
	return sig, nil
}

// CounterPath is where the local, per-root serial counter lives -- next to
// the root private key, following the same "state lives beside the key it
// belongs to" convention as rotate_keys.sh's backup directory.
func CounterPath(rootKeyPath string) string {
	return rootKeyPath + ".certserial"
}

// NextSerial reads, increments and rewrites the counter file next to the root
// key. Starts at 1 when the file does not exist yet. A write failure is
// returned as a warning string rather than an error: a serial that fails to
// persist is a smaller problem than refusing to issue a certificate at all,
// and the operator can fix the file once told.
func NextSerial(rootKeyPath string) (uint32, string, error) {
	path := CounterPath(rootKeyPath)

	var next uint32 = 1
	if data, err := os.ReadFile(path); err == nil {
		if v, perr := strconv.ParseUint(strings.TrimSpace(string(data)), 10, 32); perr == nil {
			next = uint32(v) + 1
		}
	} else if !os.IsNotExist(err) {
		return 0, "", fmt.Errorf("failed to read certificate counter %s: %w", path, err)
	}

	warning := ""
	if err := os.WriteFile(path, []byte(strconv.FormatUint(uint64(next), 10)+"\n"), 0644); err != nil {
		warning = fmt.Sprintf("failed to persist certificate counter %s: %v", path, err)
	}
	return next, warning, nil
}

// Issue builds and signs a certificate with the root key at rootKeyPath.
// leafPubHex == "" means self-signed (leaf_pubkey = the root's own public
// key); otherwise it must be a 128-hex-char raw X||Y public key belonging to
// a leaf whose private half the caller holds separately. Issuing never
// touches that private half, only its public point.
//
// A self-signed certificate takes SelfSignedSerial and leaves the counter
// alone; only delegated leaves draw a number. See SelfSignedSerial.
//
// Returns the certificate hex-encoded (264 hex chars) -- exactly what goes on
// the wire in a "flash" or "openplc_server_reboot" command -- plus a warning
// string that is empty unless the serial counter could not be written back.
func Issue(rootKeyPath string, leafPubHex string) (string, string, error) {
	root, err := LoadKey(rootKeyPath)
	if err != nil {
		return "", "", fmt.Errorf("cannot load root key %s: %w", rootKeyPath, err)
	}

	var (
		leafPub []byte
		serial  uint32
		warning string
	)
	if strings.TrimSpace(leafPubHex) == "" {
		leafPub = RawPublicKey(&root.PublicKey)
		serial = SelfSignedSerial
	} else {
		leafPub, err = hex.DecodeString(strings.TrimSpace(leafPubHex))
		if err != nil || len(leafPub) != SigLen {
			return "", "", fmt.Errorf("leaf public key must be %d hex characters", SigLen*2)
		}
		serial, warning, err = NextSerial(rootKeyPath)
		if err != nil {
			return "", "", err
		}
	}

	signed := make([]byte, SignedLen)
	copy(signed[:64], leafPub)
	binary.LittleEndian.PutUint32(signed[64:68], serial)

	digest := sha256.Sum256(signed)
	rootSig, err := SignDigest(root, digest[:])
	if err != nil {
		return "", "", fmt.Errorf("failed to sign certificate: %w", err)
	}

	cert := make([]byte, Size)
	copy(cert, signed)
	copy(cert[68:], rootSig)
	return hex.EncodeToString(cert), warning, nil
}

// NonceSig returns hex(ECDSA-sign(key, sha256(nonce || msg))). The device
// verifies it against the leaf public key named by whatever certificate
// accompanies the same command. nonceHex is what "authchallenge" /
// "openplc_server_reboot_challenge" returned; msg must be the exact command
// string being authorized.
func NonceSig(key *ecdsa.PrivateKey, nonceHex string, msg string) (string, error) {
	nonce, err := hex.DecodeString(strings.TrimSpace(nonceHex))
	if err != nil {
		return "", fmt.Errorf("invalid nonce hex %q: %w", nonceHex, err)
	}
	digest := sha256.Sum256(append(append([]byte{}, nonce...), []byte(msg)...))
	sig, err := SignDigest(key, digest[:])
	if err != nil {
		return "", fmt.Errorf("failed to sign challenge: %w", err)
	}
	return hex.EncodeToString(sig), nil
}
