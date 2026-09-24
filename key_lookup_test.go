package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// keyEnv points os.UserConfigDir and the exe directory at fresh temp dirs and
// clears any explicit key. Returns (user config dir, exe dir).
func keyEnv(t *testing.T) (string, string) {
	t.Helper()
	userDir := t.TempDir()
	exeDir := t.TempDir()
	t.Setenv("APPDATA", userDir)         // windows
	t.Setenv("XDG_CONFIG_HOME", userDir) // linux
	t.Setenv("HOME", userDir)            // darwin: $HOME/Library/Application Support
	got, err := os.UserConfigDir()
	if err != nil || !strings.HasPrefix(got, userDir) {
		t.Fatalf("UserConfigDir = %q, %v; want under %q", got, err, userDir)
	}

	oldExe, oldSigning := keyExeDir, g_signing
	keyExeDir = func() string { return exeDir }
	g_signing = signingOptions{}
	t.Cleanup(func() { keyExeDir, g_signing = oldExe, oldSigning })
	return got, exeDir
}

// writeKey generates a real P-256 key at path.
func writeKey(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := generateSigningKey(strings.TrimSuffix(path, ".pem")); err != nil {
		t.Fatal(err)
	}
}

func TestKeyLookupOrder(t *testing.T) {
	cfgDir, exeDir := keyEnv(t)
	userKey := filepath.Join(cfgDir, "openplc", "keys", "fw_signing_key.pem")
	exeKey := filepath.Join(exeDir, "keys", "fw_signing_key.pem")
	pubKey := filepath.Join(exeDir, "keys", "published_root.TEST_ONLY.pem")

	// Nothing anywhere.
	if p, _ := findUploadKey(); p != "" {
		t.Fatalf("no keys: got %q", p)
	}
	if got := defaultKeyLocation(); got != userKey {
		t.Fatalf("defaultKeyLocation = %q, want %q", got, userKey)
	}

	// Published key only: uploads take it and say so; other commands do not.
	writeKey(t, pubKey)
	if p, published := findUploadKey(); p != pubKey || !published {
		t.Fatalf("published only: got %q published=%v", p, published)
	}
	if p := findSigningKey(); p != "" {
		t.Fatalf("findSigningKey must never return the published key, got %q", p)
	}

	// Exe dir beats the published key.
	writeKey(t, exeKey)
	if p, published := findUploadKey(); p != exeKey || published {
		t.Fatalf("exe dir: got %q published=%v", p, published)
	}

	// User dir beats the exe dir.
	writeKey(t, userKey)
	if p, published := findUploadKey(); p != userKey || published {
		t.Fatalf("user dir: got %q published=%v", p, published)
	}

	// --key / local_config.json beats everything.
	g_signing.keyPath = filepath.Join(t.TempDir(), "explicit.pem")
	if p, _ := findUploadKey(); p != g_signing.keyPath {
		t.Fatalf("explicit: got %q", p)
	}
}

func TestUploadIdentityNoKeyNamesUserDir(t *testing.T) {
	cfgDir, _ := keyEnv(t)
	_, err := resolveUploadIdentity()
	want := filepath.Join(cfgDir, "openplc", "keys", "fw_signing_key.pem")
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Fatalf("error %v does not name %s", err, want)
	}
}

func TestUploadIdentityFallsBackToPublishedKey(t *testing.T) {
	_, exeDir := keyEnv(t)
	pubKey := filepath.Join(exeDir, "keys", "published_root.TEST_ONLY.pem")
	writeKey(t, pubKey)
	id, err := resolveUploadIdentity()
	if err != nil {
		t.Fatal(err)
	}
	if id.keyPath != pubKey || id.key == nil || id.certHex == "" {
		t.Fatalf("identity = %+v", id)
	}
}
