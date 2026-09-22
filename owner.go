package main

// Board ownership: claim a board for a customer's own signing key, hand it
// over to another key, and ask which key it currently trusts.
//
// The bootloader answers these commands on the same TCP port the upload uses,
// one exchange per connection because it serves a single client at a time:
//
//   getpubkey                          the 64-byte root public key, hex
//   getowner                           generation of the record in force, 0 = unclaimed
//   takeown  <pubkey>                  claim; refused unless BOOT0 was held at startup
//   setowner <gen> <pubkey> <sig>      hand over; refused unless the CURRENT owner signed
//   revoke   <leafhex> <sig>           revoke one leaf; refused unless the CURRENT owner signed
//
// See $PROD/docs/modules/M2-ownership.md for why the first claim is
// gated on a button and every later one on a signature. Revocation names a
// leaf by the first 16 bytes of its public key, not a tool-assigned number --
// see $PROD/maps/owner-revoke-and-boot-upgrade/issues/OWN-01-revoke-by-serial-or-by-pubkey.md.

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

// Mirrors the owner area in open_plc_cube_ide/IAPServer/owner_slot.h: an 'O'
// segment of 160-byte ownership records and an 'R' segment of 32-byte
// revocation records, sharing one format_ver. Only the bytes the board signs
// over are built here -- the first 88 of an 'O' record
// (type/reserved0/format_ver/generation/flags/root_pubkey/uid), the whole 32
// of an 'R' record. Layout: $PROD/docs/modules/M2-ownership.md.
const (
	ownerRecordType       = 'O'
	ownerRecordTypeRevoke = 'R'
	ownerRecordReserved0  = 0
	ownerRecordFormatVer  = 4
	ownerUIDLen           = 12
	ownerSignedPrefixLen  = 88
	ownerRevokePrefixLen  = 16
	ownerRevokeRecordLen  = 32
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
	b.WriteByte(ownerRecordReserved0)
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

// ownerRevokeSignedPrefix builds the whole 32-byte 'R' record: type,
// reserved0, format_ver, the board's own uid, the revoked leaf's 16-byte
// prefix. The board verifies the signature at write time and then discards it,
// so the signed bytes are exactly the bytes it writes, reserved0 included.
// Layout: $PROD/docs/modules/M2-ownership.md.
func ownerRevokeSignedPrefix(leafPrefixHex string, uidHex string) ([]byte, error) {
	prefix, err := hex.DecodeString(leafPrefixHex)
	if err != nil || len(prefix) != ownerRevokePrefixLen {
		return nil, fmt.Errorf("the leaf prefix must be %d hex characters", ownerRevokePrefixLen*2)
	}
	uid, err := hex.DecodeString(strings.TrimSpace(uidHex))
	if err != nil || len(uid) != ownerUIDLen {
		return nil, fmt.Errorf("the board's uid must be %d hex characters, got %q", ownerUIDLen*2, uidHex)
	}

	var b bytes.Buffer
	b.WriteByte(ownerRecordTypeRevoke)
	b.WriteByte(ownerRecordReserved0)
	binary.Write(&b, binary.LittleEndian, uint16(ownerRecordFormatVer))
	b.Write(uid)
	b.Write(prefix)
	if b.Len() != ownerRevokeRecordLen {
		return nil, fmt.Errorf("built a %d byte record, expected %d", b.Len(), ownerRevokeRecordLen)
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

	// Without these lines the output reads like a dead end. It is not: a board
	// that was just factory-reset still reports a non-zero generation (the reset
	// writes a record of its own), so "Claimed at generation N" on its own
	// misleads. The wire protocol cannot say "cleared" and deliberately will not
	// be extended to -- see $PROD/docs/modules/M2-ownership.md.
	fmt.Println()
	fmt.Println("To hand this board to a different key:")
	fmt.Println("  setowner - signed by the current owner's key, no button needed")
	fmt.Println("  takeown  - after a factory reset (hold BOOT0), physical presence required")
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
	// Reported, not enforced. "getowner" answers a generation and nothing else,
	// so a board cleared by a factory reset is indistinguishable here from a
	// claimed one -- its generation keeps counting. Refusing on gen != 0 turned
	// a board that the bootloader would have accepted (owner_slot_claim()
	// allows a cleared record) into one that could not be claimed at all.
	// The board makes the decision and says why; this only says what we saw.
	// Issue: $PROD/maps/owner-revoke-and-boot-upgrade/issues/OWN-11-getowner-cannot-say-cleared.md
	if gen != 0 {
		fmt.Printf("This board reports generation %d, key %s...\n", gen, was[:32])
		fmt.Println("If it is still claimed the board will refuse, and setowner with the")
		fmt.Println("current owner's key is the way to hand it over. If it was cleared by a")
		fmt.Println("factory reset the claim goes through -- the board decides, not this tool.")
		fmt.Println()
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
// RunSetOwner hands a claimed board to newKeyPath, signed by currentKeyPath.
//
// With wipe, the board erases sector 0 and rewrites it with its own bootloader
// and an owner area holding nothing but the new record. That is the only way
// to get revocation slots back, and it costs a reset plus the risk that a
// power cut during the erase leaves the board needing a DFU re-flash. The
// board never decides to do this on its own -- the operator asks for it
// (OWN-07).
func RunSetOwner(ip, currentKeyPath, newKeyPath string, wipe bool) {
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

	verb := "setowner"
	if wipe {
		verb = "setownerwipe"
		fmt.Println("  --wipe: the board will erase and rewrite its own flash sector.")
		fmt.Println("  DO NOT CUT POWER. If it is interrupted, hold BOOT0 through a reset")
		fmt.Println("  to reach the ST ROM DFU and re-flash the bootloader over USB.")
	}

	reply, err := ownerCommand(ip, fmt.Sprintf("%s %d %s %s", verb, next, newPub, strings.TrimSpace(sig)))
	if wipe {
		// A successful wipe never answers: the board resets as soon as the
		// new sector is written. An answer therefore means it refused, and a
		// dropped connection is the expected outcome.
		if err == nil && strings.Contains(strings.TrimSpace(reply), "Refused") {
			logf(true, "The board refused the wipe and erased nothing: %s", strings.TrimSpace(reply))
		}
		fmt.Println("The board is rewriting its flash and will reset. Give it a few seconds,")
		fmt.Printf("then check with: IAPTool getowner %s\n", ip)
		return
	}
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

// RunRevoke revokes one leaf, named by its public key, signed by the current
// owner. currentKeyPath is the private half of the root the board trusts
// today -- the same role --current-key plays in RunSetOwner.
func RunRevoke(ip, currentKeyPath, leafPubHex string) {
	if strings.TrimSpace(currentKeyPath) == "" || strings.TrimSpace(leafPubHex) == "" {
		logf(true, "revoke needs both:\n"+
			"  --key=<owner.pem>   the current owner's key, to sign the revocation\n"+
			"  --leaf=<pubkey hex> the leaf to revoke, 128 hex characters")
	}
	leafPub, err := hex.DecodeString(strings.TrimSpace(leafPubHex))
	if err != nil || len(leafPub) != 64 {
		logf(true, "--leaf must be 128 hex characters (a raw secp256r1 public key), got %q", leafPubHex)
	}
	leafPrefixHex := hex.EncodeToString(leafPub[:ownerRevokePrefixLen])

	currentPub, err := ownerPublicKeyHex(currentKeyPath)
	logf(err, "cannot use the current owner key")

	gen, trusted, err := ownerReadState(ip)
	logf(err, "cannot read this board's ownership state")
	if gen == 0 {
		// gen is read only to spot an unclaimed board; a revocation takes no
		// generation of its own.
		logf(true, "This board is unclaimed - there is no owner to sign a revocation. Use takeown.")
	}
	if trusted != strings.ToLower(currentPub) {
		logf(true, "The board does not trust the key in %s:\n  board trusts  %s\n  you offered   %s\n"+
			"Only the current owner can revoke a leaf.", currentKeyPath, trusted, currentPub)
	}

	uidHex, err := ownerGetUID(ip)
	logf(err, "cannot read this board's UID")

	prefix, err := ownerRevokeSignedPrefix(leafPrefixHex, uidHex)
	logf(err, "cannot build the revocation record")

	sig, err := signRawHex(hex.EncodeToString(prefix), currentKeyPath)
	logf(err, "cannot sign the revocation")

	fmt.Printf("Revoking one leaf. The board stays at owner generation %d - a revocation does not take one.\n", gen)
	fmt.Printf("  leaf (first %d bytes): %s\n", ownerRevokePrefixLen, leafPrefixHex)

	reply, err := ownerCommand(ip, fmt.Sprintf("revoke %s %s", leafPrefixHex, strings.TrimSpace(sig)))
	logf(err, "the revoke command did not get through")
	reply = strings.TrimSpace(reply)
	if !strings.Contains(reply, Rsp_OK) {
		logf(true, "The board refused: %s", reply)
	}

	// The board's OK already means it wrote the record, re-scanned the owner
	// area and confirmed every name just written now reads back as revoked
	// (owner_slot_revoke, IAPServer/owner_slot.c). Do NOT re-check the
	// generation here: a revoke record never becomes the effective owner
	// record, so the effective generation deliberately does not move, and
	// requiring it to advance reports a successful revocation as a failure.
	// What is worth reading back is that the root did not change -- a
	// revocation must never hand the board to somebody else.
	_, now, err := ownerReadState(ip)
	logf(err, "revoked, but reading the state back failed")
	if now != strings.ToLower(currentPub) {
		logf(true, "The board answered OK but now trusts %s, not %s -\n"+
			"a revocation must not change the root.", now, strings.ToLower(currentPub))
	}
	if strings.Contains(reply, "already revoked") {
		fmt.Println("That leaf was already revoked; nothing was written.")
	}
	fmt.Printf("Done. The board still trusts %s;\n", now)
	fmt.Println("any firmware certified by the revoked leaf is refused from the next reset on.")
}
