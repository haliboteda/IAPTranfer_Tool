package main

// Board ownership: claim a board for a customer's own signing key, hand it
// over to another key, and ask which key it currently trusts.
//
// The bootloader answers three commands on the same TCP port the upload uses,
// one exchange per connection because it serves a single client at a time:
//
//   getpubkey                          the 64-byte root public key, hex
//   getowner                           generation of the record in force, 0 = unclaimed
//   takeown  <pubkey>                  claim; refused unless BOOT0 was held at startup
//   setowner <gen> <pubkey> <sig>      hand over; refused unless the CURRENT owner signed
//
// See $PROD/docs/modules/M2-ownership.md for why the first claim is
// gated on a button and every later one on a signature.

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"net"
	"regexp"
	"strconv"
	"strings"
)

// Mirrors owner_record_t in open_plc_cube_ide/IAPServer/owner_slot.h. Only
// the first 88 bytes (type/slots/format_ver/generation/flags/root_pubkey/uid)
// are signed, so only they are built here.
//
// 2026-09-04: format_ver 1 -> 2, ownerSignedPrefixLen 76 -> 88, for the uid
// field (see ownerSignedPrefix). No v1 compatibility on the board side, so
// none is needed here either.
const (
	ownerRecordType      = 'O'
	ownerRecordSlots     = 5
	ownerRecordFormatVer = 2
	ownerUIDLen          = 12
	ownerSignedPrefixLen = 88
)

var pubKeyPattern = regexp.MustCompile(`^[0-9a-fA-F]{128}$`)

// ownerCommand runs one command and returns the device's reply.
func ownerCommand(ip, cmd string) (string, error) {
	conn, err := net.DialTimeout("tcp", net.JoinHostPort(ip, getPort()), Timeout)
	if err != nil {
		return "", fmt.Errorf("cannot reach %s: %v", ip, err)
	}
	defer conn.Close()
	return sendAndReadResponse(conn, []byte(cmd+"\n"))
}

// ownerPublicKeyHex reads a PEM private key and returns its public half in the
// form the device speaks.
func ownerPublicKeyHex(keyPath string) (string, error) {
	key, err := loadSigningKey(keyPath)
	if err != nil {
		return "", fmt.Errorf("cannot read %s: %v", keyPath, err)
	}
	return publicKeyHex(&key.PublicKey), nil
}

// ownerReadState returns the generation in force and the trusted key.
func ownerReadState(ip string) (uint32, string, error) {
	genReply, err := ownerCommand(ip, "getowner")
	if err != nil {
		return 0, "", err
	}
	key, err := ownerCommand(ip, "getpubkey")
	if err != nil {
		return 0, "", err
	}
	key = strings.TrimSpace(key)
	if !pubKeyPattern.MatchString(key) {
		return 0, "", fmt.Errorf("the board answered getpubkey with %q - is it in the bootloader?", key)
	}
	gen, err := strconv.ParseUint(strings.TrimSpace(genReply), 10, 32)
	if err != nil {
		return 0, "", fmt.Errorf("the board answered getowner with %q", genReply)
	}
	return uint32(gen), strings.ToLower(key), nil
}

// ownerSignedPrefix builds the 88 bytes the next owner record is signed
// over: type, slots, format_ver, generation, flags, the incoming public key,
// and (v2) the target board's own uid -- the board fills uid into the record
// itself from its own hardware UID when it writes it, so the signature has
// to cover the exact bytes the board will end up with, or verification fails
// the moment the board recomputes sha256 over its own copy.
func ownerSignedPrefix(generation uint32, pubKeyHex string, uidHex string) ([]byte, error) {
	pub, err := hex.DecodeString(pubKeyHex)
	if err != nil || len(pub) != 64 {
		return nil, fmt.Errorf("the new key must be 128 hex characters")
	}
	uid, err := hex.DecodeString(strings.TrimSpace(uidHex))
	if err != nil || len(uid) != ownerUIDLen {
		return nil, fmt.Errorf("the board's uid must be %d hex characters, got %q", ownerUIDLen*2, uidHex)
	}
	var b bytes.Buffer
	b.WriteByte(ownerRecordType)
	b.WriteByte(ownerRecordSlots)
	binary.Write(&b, binary.LittleEndian, uint16(ownerRecordFormatVer))
	binary.Write(&b, binary.LittleEndian, generation)
	binary.Write(&b, binary.LittleEndian, uint32(0)) // flags
	b.Write(pub)
	b.Write(uid)
	if b.Len() != ownerSignedPrefixLen {
		return nil, fmt.Errorf("built a %d byte prefix, expected %d", b.Len(), ownerSignedPrefixLen)
	}
	return b.Bytes(), nil
}

// ownerGetUID asks the board for its own hardware UID ("getuid"), the same
// value it will fill into a new owner record's uid field itself.
func ownerGetUID(ip string) (string, error) {
	reply, err := ownerCommand(ip, "getuid")
	if err != nil {
		return "", err
	}
	uidHex := strings.ToLower(strings.TrimSpace(reply))
	if _, err := hex.DecodeString(uidHex); err != nil || len(uidHex) != ownerUIDLen*2 {
		return "", fmt.Errorf("the board answered getuid with %q, expected %d hex characters", reply, ownerUIDLen*2)
	}
	return uidHex, nil
}

// RunGetOwner prints which key the board trusts and how it got there.
func RunGetOwner(ip string) {
	gen, key, err := ownerReadState(ip)
	logf(err, "cannot read this board's ownership state")

	if gen == 0 {
		fmt.Println("Unclaimed. The board still trusts the key built into its bootloader.")
	} else {
		fmt.Printf("Claimed at generation %d.\n", gen)
	}
	fmt.Printf("Trusted key: %s\n", key)
	fmt.Println("Only firmware signed by that key will start.")
}

// RunTakeOwn claims an unclaimed board for the key in keyPath.
func RunTakeOwn(ip, keyPath string) {
	if strings.TrimSpace(keyPath) == "" {
		logf(true, "takeown needs --key=<owner.pem>, the key this board should trust from now on.\n"+
			"Generate one first:  IAPTool genkey owner\n"+
			"There is deliberately no default: claiming a board with the wrong key is hard to undo.")
	}
	pub, err := ownerPublicKeyHex(keyPath)
	logf(err, "cannot use that key")

	gen, was, err := ownerReadState(ip)
	logf(err, "cannot read this board's ownership state")
	if gen != 0 {
		logf(true, "This board is already claimed (generation %d, key %s...).\n"+
			"Use setowner with the current owner's key to hand it over.", gen, was[:32])
	}

	fmt.Println("About to claim this board for:")
	fmt.Printf("  %s\n", pub)
	fmt.Println("From then on it runs only firmware signed by that key, and the only way")
	fmt.Println("back is to reflash the bootloader over ST-Link - the owner records live")
	fmt.Println("in the bootloader's own flash sector.")
	fmt.Println()
	fmt.Println("The board must have BOOT0 held through THIS boot: the first claim carries")
	fmt.Println("no signature, so physical presence is the only gate there can be.")
	fmt.Println("If it was not held, the board answers Refused and nothing changes.")
	fmt.Println()

	reply, err := ownerCommand(ip, "takeown "+pub)
	logf(err, "the takeown command did not get through")
	reply = strings.TrimSpace(reply)

	if !strings.Contains(reply, Rsp_OK) {
		logf(true, "The board refused: %s\n"+
			"Press RESET, hold BOOT0 until start-up finishes, let go, then try again.", reply)
	}

	gen, now, err := ownerReadState(ip)
	logf(err, "claimed, but reading the state back failed")
	if now != strings.ToLower(pub) {
		logf(true, "The board answered OK but reports a different key:\n  claimed  %s\n  reports  %s", pub, now)
	}
	fmt.Printf("Claimed at generation %d. Keep %s safe - it is now the only key\n", gen, keyPath)
	fmt.Println("that can produce firmware this board will run.")
}

// RunSetOwner hands a claimed board over to a new key, signed by the current one.
func RunSetOwner(ip, currentKeyPath, newKeyPath string) {
	if strings.TrimSpace(currentKeyPath) == "" || strings.TrimSpace(newKeyPath) == "" {
		logf(true, "setowner needs both keys:\n"+
			"  --current-key=<owner.pem>  the key the board trusts today, to sign the handover\n"+
			"  --new-key=<next.pem>       the key it should trust from now on")
	}
	newPub, err := ownerPublicKeyHex(newKeyPath)
	logf(err, "cannot use the new key")
	currentPub, err := ownerPublicKeyHex(currentKeyPath)
	logf(err, "cannot use the current owner key")

	gen, trusted, err := ownerReadState(ip)
	logf(err, "cannot read this board's ownership state")
	if gen == 0 {
		logf(true, "This board is unclaimed - there is no owner to sign a handover. Use takeown.")
	}
	if trusted != strings.ToLower(currentPub) {
		logf(true, "The board does not trust the key in %s:\n  board trusts  %s\n  you offered   %s\n"+
			"Only the current owner can hand the board over.", currentKeyPath, trusted, currentPub)
	}

	uidHex, err := ownerGetUID(ip)
	logf(err, "cannot read this board's UID")

	next := gen + 1
	prefix, err := ownerSignedPrefix(next, newPub, uidHex)
	logf(err, "cannot build the owner record")

	sig, err := signRawHex(hex.EncodeToString(prefix), currentKeyPath)
	logf(err, "cannot sign the handover")

	fmt.Printf("Handing over at generation %d -> %d\n", gen, next)
	fmt.Printf("  new key: %s\n", newPub)

	reply, err := ownerCommand(ip, fmt.Sprintf("setowner %d %s %s", next, newPub, strings.TrimSpace(sig)))
	logf(err, "the setowner command did not get through")
	reply = strings.TrimSpace(reply)
	if !strings.Contains(reply, Rsp_OK) {
		logf(true, "The board refused: %s", reply)
	}

	gotGen, now, err := ownerReadState(ip)
	logf(err, "handed over, but reading the state back failed")
	if gotGen != next || now != strings.ToLower(newPub) {
		logf(true, "The board answered OK but reports generation %d and key %s", gotGen, now)
	}
	fmt.Printf("Done. Generation %d, and the board now runs only firmware signed by %s.\n", gotGen, newKeyPath)
}
