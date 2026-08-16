// S1: the pure signature-verification path.
//
// IAPTool cannot produce this case by design -- its getpubkey pre-check refuses
// to transfer an image the board will not accept. So this drives the protocol
// directly, using the shipping crypto package (IAPTool/iapcrypto) rather than a
// reimplementation: only the *signature* is deliberately wrong, everything else
// (auth HMAC, CRC, framing) is exactly what IAPTool would send.
package main

import (
	"encoding/hex"
	"fmt"
	"hash/crc32"
	"net"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"IAPTool/iapcrypto"
)

const (
	chunkSize     = 8192
	verifyTimeout = 30 * time.Second
)

func init() {
	// Not destructive since SDRAM staging landed: the image is verified in the
	// staging buffer and the application region is only erased once it passes,
	// so a refused upload leaves the running application intact. Verified on
	// hardware 2026-08-17 -- S1 refused the image, and the board booted its
	// existing application after a reset.
	//
	// Before staging this case did leave the board unable to boot, which is why
	// it used to be flagged destructive and sorted last by "all".
	register(testCase{id: "S1", title: "an image with an invalid signature is rejected",
		destructive: false, run: runS1})
}

var blockCommentRe = regexp.MustCompile(`(?s)/\*.*?\*/`)

// The shared password file holds the password as a C string literal, because
// the firmware #includes it directly.
func loadFixedPassword(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	text := blockCommentRe.ReplaceAllString(string(data), " ")
	start := strings.IndexByte(text, '"')
	if start < 0 {
		return fmt.Errorf("no quoted password in %s", path)
	}
	end := start + 1
	for end < len(text) && text[end] != '"' {
		if text[end] == '\\' {
			end++
		}
		end++
	}
	if end >= len(text) {
		return fmt.Errorf("unterminated password literal in %s", path)
	}
	pw, err := strconv.Unquote(text[start : end+1])
	if err != nil {
		return fmt.Errorf("invalid password literal in %s: %w", path, err)
	}
	if pw == "" {
		return fmt.Errorf("password in %s is empty", path)
	}
	iapcrypto.SetFixedPassword([]byte(pw))
	return nil
}

// ask sends one text command and returns the reply.
func ask(conn net.Conn, cmd string, timeout time.Duration) (string, error) {
	if err := conn.SetDeadline(time.Now().Add(timeout)); err != nil {
		return "", err
	}
	if _, err := conn.Write([]byte(cmd + "\n")); err != nil {
		return "", fmt.Errorf("write %q: %w", cmd, err)
	}
	buf := make([]byte, 512)
	n, err := conn.Read(buf)
	if err != nil {
		return "", fmt.Errorf("read after %q: %w", cmd, err)
	}
	return strings.TrimSpace(string(buf[:n])), nil
}

func runS1(cfg config) result {
	if cfg.binPath == "" {
		return fail("needs --bin=<file.bin>")
	}
	if cfg.passwordFile == "" {
		return fail("needs --password-file=<iap_fixed_password.txt> (same password the board was built with)")
	}
	if err := loadFixedPassword(cfg.passwordFile); err != nil {
		return fail("could not load the password file: %v", err)
	}

	image, err := os.ReadFile(cfg.binPath)
	if err != nil {
		return fail("could not read %s: %v", cfg.binPath, err)
	}
	if len(image) < chunkSize {
		return fail("image is only %d bytes, too small to be a real app", len(image))
	}

	// Change the image so it also stops matching the metadata already stored on
	// the board. That way this one run exercises both halves of the signature
	// path: the upload-time check now, and the boot-time check on the next reset.
	image[len(image)/2] ^= 0xFF

	conn, err := dial(cfg)
	if err != nil {
		return fail("could not connect: %v", err)
	}
	defer conn.Close()

	if err := alive(conn); err != nil {
		return fail("board did not answer ping: %v", err)
	}

	uidHex, err := ask(conn, "getuid", dialTimeout)
	if err != nil {
		return fail("getuid failed: %v", err)
	}
	uid, err := hex.DecodeString(strings.TrimSpace(uidHex))
	if err != nil {
		return fail("board reported an unusable UID %q: %v", uidHex, err)
	}
	deviceKey := iapcrypto.DeriveDeviceKey(uid)
	fmt.Printf("    target UID=%s\n", uidHex)

	// 64 zero bytes: not a signature any key could ever produce.
	sigHex := strings.Repeat("00", 64)
	checksum := crc32.ChecksumIEEE(image)
	base := fmt.Sprintf("flash %d %x %s", len(image), checksum, sigHex)

	authMsg := base
	flashCmd := ""
	if version, ok := readVersionFile(cfg.binPath); ok {
		authMsg = fmt.Sprintf("%s %d", base, version)
	}

	nonceHex, err := ask(conn, "authchallenge", dialTimeout)
	if err != nil {
		return fail("authchallenge failed: %v", err)
	}
	nonce, err := hex.DecodeString(strings.TrimSpace(nonceHex))
	if err != nil {
		return fail("board returned an unusable nonce %q: %v", nonceHex, err)
	}
	mac := hex.EncodeToString(iapcrypto.HMACSHA256(deviceKey, append(append([]byte{}, nonce...), []byte(authMsg)...)))

	if version, ok := readVersionFile(cfg.binPath); ok {
		flashCmd = fmt.Sprintf("%s %s %d", base, mac, version)
	} else {
		flashCmd = fmt.Sprintf("%s %s", base, mac)
	}

	// The command itself must authenticate: a rejected command would prove
	// nothing about signature verification.
	reply, err := ask(conn, flashCmd, verifyTimeout)
	if err != nil {
		return fail("flash command failed: %v", err)
	}
	if !strings.Contains(reply, "OK") {
		return fail("board rejected the flash command itself (%q) -- this case needs it accepted, "+
			"otherwise nothing about signature checking is proven", reply)
	}
	fmt.Printf("    flash command accepted, sending %d bytes with a bogus signature...\n", len(image))

	for sent := 0; sent < len(image); {
		end := sent + chunkSize
		if end > len(image) {
			end = len(image)
		}
		if err := conn.SetDeadline(time.Now().Add(verifyTimeout)); err != nil {
			return fail("set deadline: %v", err)
		}
		if _, err := conn.Write(image[sent:end]); err != nil {
			return fail("sending bytes at offset %d: %v", sent, err)
		}
		buf := make([]byte, 256)
		n, err := conn.Read(buf)
		if err != nil {
			return fail("no reply after the chunk at offset %d: %v", sent, err)
		}
		sent = end

		// The last chunk is answered with "OK" and then the verdict, which may
		// arrive in the same read.
		if sent >= len(image) {
			verdict := strings.TrimSpace(string(buf[:n]))
			if !mentionsVerdict(verdict) {
				if err := conn.SetDeadline(time.Now().Add(verifyTimeout)); err != nil {
					return fail("set deadline: %v", err)
				}
				n, err = conn.Read(buf)
				if err != nil {
					return fail("board never reported a verdict: %v", err)
				}
				verdict = strings.TrimSpace(string(buf[:n]))
			}
			return judgeVerdict(verdict)
		}
	}
	return fail("ran out of image without a verdict")
}

func mentionsVerdict(s string) bool {
	return strings.Contains(s, "Signature Failed") ||
		strings.Contains(s, "No Signature") ||
		strings.Contains(s, "Checksum Failed")
}

func judgeVerdict(verdict string) result {
	switch {
	case strings.Contains(verdict, "Signature Failed"), strings.Contains(verdict, "No Signature"):
		return pass("board refused the image: %q. The application region was never touched, "+
			"so the previously-installed application still boots -- reset to confirm (case G1)", verdict)
	case strings.Contains(verdict, "Checksum Failed"):
		return fail("board reported a checksum failure (%q), so the signature check never ran -- "+
			"the CRC this tool computed does not match what the board computed", verdict)
	default:
		return fail("board accepted an image signed with 64 zero bytes (replied %q). "+
			"Signature verification is not gating the update", verdict)
	}
}

func readVersionFile(binPath string) (uint32, bool) {
	path := strings.TrimSuffix(binPath, ".bin") + ".version"
	data, err := os.ReadFile(path)
	if err != nil {
		return 0, false
	}
	v, err := strconv.ParseUint(strings.TrimSpace(string(data)), 10, 32)
	if err != nil {
		return 0, false
	}
	return uint32(v), true
}
