package main

// Leaf certificate issuance for the command paths. The format and the crypto
// live in iapcert/ so the test cases import the same code the tool ships with.

import (
	"IAPTool/iapcert"
)

// issueLeafCert issues a certificate with the signing key at rootKeyPath as
// root, self-signed when leafPubHex is empty. A counter that could not be
// written back is reported and does not stop issuance -- the same
// tolerate-and-continue style as uploadlock.go.
func issueLeafCert(rootKeyPath string, leafPubHex string) (string, error) {
	certHex, warning, err := iapcert.Issue(rootKeyPath, leafPubHex)
	if warning != "" {
		logf("Warning: %s", warning)
	}
	return certHex, err
}
