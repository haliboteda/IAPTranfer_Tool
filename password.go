package main

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"IAPTool/iapcrypto"
)

// The shared password file, byte-identical in all three places it lives:
// IAPServer/keys/ (compiled into the bootloader), the Arduino core's
// OpenPLC_IAP/src/keys/ (compiled into the app), and <exe dir>/keys/ (read
// here at run time). Rotating it therefore needs no rebuild of this tool.
const passwordFileName = "iap_fixed_password.txt"

// findPasswordFile mirrors findSigningKey: an explicit --password-file or
// local_config.json "password_file" wins, otherwise the file under
// <exe dir>/keys.
func findPasswordFile() string {
	if g_signing.passwordPath != "" {
		return g_signing.passwordPath
	}
	if configured := strings.TrimSpace(l_config.PasswordFile); configured != "" {
		return configured
	}
	exeDir := GetCurDir()
	if exeDir == "" {
		return ""
	}
	return filepath.Join(exeDir, keysDirName, passwordFileName)
}

var blockCommentRe = regexp.MustCompile(`(?s)/\*.*?\*/`)

// parsePasswordFile pulls the C string literal out of the shared password
// file -- the very same text the firmware #includes as its initialiser, so
// both sides are guaranteed to derive keys from identical bytes.
func parsePasswordFile(data []byte) ([]byte, error) {
	text := blockCommentRe.ReplaceAllString(string(data), " ")

	start := strings.IndexByte(text, '"')
	if start < 0 {
		return nil, fmt.Errorf("no quoted password found (the password must stay in double quotes)")
	}
	end := start + 1
	for end < len(text) && text[end] != '"' {
		if text[end] == '\\' {
			end++
		}
		end++
	}
	if end >= len(text) {
		return nil, fmt.Errorf("unterminated password literal")
	}

	password, err := strconv.Unquote(text[start : end+1])
	if err != nil {
		return nil, fmt.Errorf("invalid password literal: %w", err)
	}
	if password == "" {
		return nil, fmt.Errorf("password is empty")
	}
	return []byte(password), nil
}

// loadFixedPassword installs the password used to derive every device key.
// Only the flashing modes need it; sign/genkey/genpw do not, so it is
// resolved there rather than at start-up.
func loadFixedPassword() {
	path := findPasswordFile()
	if path == "" {
		logf(true, "Could not determine where to look for %s", passwordFileName)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		logf(true, "No IAP password file at %s.\n"+
			"  It ships next to this tool and must hold the same password that was\n"+
			"  compiled into the board's firmware. Restore it, pass\n"+
			"  --password-file=<path>, or set \"password_file\" in local_config.json",
			path)
	}

	password, err := parsePasswordFile(data)
	if err != nil {
		logf(true, "Invalid password file %s: %v", path, err)
	}
	iapcrypto.SetFixedPassword(password)
}

// generatePasswordFile returns a fresh random password already wrapped in the
// shared file format, ready to be written to all three keys/ directories.
// base64url keeps the literal free of characters that would need escaping in C.
func generatePasswordFile() (string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", fmt.Errorf("failed to read random bytes: %w", err)
	}

	return fmt.Sprintf(`/* IAP fixed password. Shared by the bootloader, the Arduino app and IAPTool.
 * Rotate with IAPServer/keys/rotate_keys.sh -- do not hand-edit. Keep the
 * quotes: this file is #included directly as a C string literal. */
%q
`, base64.RawURLEncoding.EncodeToString(raw)), nil
}
