package main

import (
	"fmt"
	"os"
	"strings"
)

// json file struct
type LocalConfig struct {
	BaudRate          int    `json:"BaudRate"`
	Parity            int    `json:"Parity"`
	DataBits          int    `json:"DataBits"`
	StopBits          int    `json:"StopBits"`
	ReadTimeout       int    `json:"ReadTimeout"`
	UID               string `json:"uid"`
	BootIP            string `json:"bootIP"`
	AppIP             string `json:"appIP"`
	MAC               string `json:"mac"`
	ServerPort        string `json:"server_port"`
	RebootWaitSeconds int    `json:"reboot_wait_seconds"`
	SigningKey        string `json:"signing_key"`
}

var l_config LocalConfig

// signingOptions holds the command-line signing overrides for this run.
type signingOptions struct {
	keyPath    string
	certPath   string
	outPrefix  string
	currentKey string
	newKey     string
	// keyExplicit says --key was actually typed. takeown must not fall back to
	// the firmware signing key from local_config.json: claiming a customer's
	// board with the project's own key is not recoverable without an ST-Link.
	keyExplicit bool
}

var g_signing signingOptions

const usageText = `Usage:
  IAPTool cdc    <file.bin> <port>       [--key=<key.pem>] [--cert=<cert.txt>]
  IAPTool ether  <file.bin> <ip>         [--key=<key.pem>] [--cert=<cert.txt>]
  IAPTool sign   <file.bin> [<key.pem>]  [--key=<key.pem>] [--out=<prefix>]
  IAPTool genkey [<name>]    writes <name>.pem, prints keys/fw_pubkey.inc on stdout
  IAPTool pubkey [<key.pem>] the key's public half as 128 hex characters, the form
                   "cert" and "getpubkey" speak. Send this to whoever holds the
                   root when you need a certificate issued for your key.
  IAPTool cert   [<leafPubHex>]
                   issues a 132-byte leaf certificate signed by the signing key,
                   hex on stdout. No argument = self-signed (simple mode: the key
                   authorises itself); a 128-hex-char public key = delegated leaf.
                   The serial comes from a counter file kept next to the key.
  IAPTool signraw <hex> [<key.pem>]  raw r||s signature over SHA-256 of those
                   bytes, hex on stdout. For the bootloader's owner-record
                   chain (setowner), not for firmware images.
  IAPTool getowner <ip>      which key this board trusts, and at which generation
  IAPTool takeown  <ip> --key=<owner.pem>
                   claims an unclaimed board for that key. BOOT0 must have been
                   held through the board's current boot - the first claim carries
                   no signature, so presence is the only gate. Hard to undo.
  IAPTool setowner <ip> --current-key=<owner.pem> --new-key=<next.pem>
                   hands a claimed board over to another key. The handover is
                   signed by the current owner, so no button is needed.

  --key            ECDSA P-256 private key (PEM). When omitted, falls back to
                   "signing_key" in local_config.json, then to keys/fw_signing_key.pem
                   next to this executable. Uploading needs the private key itself:
                   the image is signed in memory and so is the board's challenge.
  --cert           Certificate presented to the board, as issued by the holder of
                   the root it trusts. When omitted, falls back to "<the signing
                   key>.cert", and with no certificate there the signing key
                   certifies itself - which is what one person with one key wants.
  --out            Output prefix for "sign". Defaults to the .bin path without its extension.

To rotate the signing key, run IAPServer/keys/rotate_keys.sh.`

func main() {
	// Load config from JSON file
	LoadConfig()

	args, err := parseSigningFlags(os.Args[1:])
	if err != nil {
		logf(true, "%v", err)
	}
	if g_signing.keyPath == "" {
		g_signing.keyPath = strings.TrimSpace(l_config.SigningKey)
	}

	if len(args) < 1 {
		logf(true, usageText)
	}
	mode := strings.ToLower(args[0])

	switch mode {
	case "sign":
		if len(args) < 2 {
			logf(true, usageText)
		}
		// A positional key overrides --key/config, keeping the argument
		// order of the shell script it replaces.
		keyPath := findSigningKey()
		if len(args) >= 3 {
			keyPath = args[2]
		}
		if keyPath == "" {
			logf(true, "No signing key found. Pass one as an argument or as --key=<key.pem>, "+
				"put one at %s, or set \"signing_key\" in local_config.json", defaultKeyLocation())
		}
		err := signBinFile(args[1], keyPath, g_signing.outPrefix)
		logf(err, "Failed to sign %s", args[1])

	case "genkey":
		name := "fw_signing_key"
		if len(args) >= 2 {
			name = args[1]
		}
		err := generateSigningKey(name)
		logf(err, "Failed to generate signing key")

	case "pubkey":
		// The one form a root holder can act on: "cert <leafPubHex>" takes
		// exactly these 128 characters, and so does comparing against what a
		// board answers to getpubkey. genkey prints the C initialiser instead,
		// which is the wrong shape for both.
		keyPath := findSigningKey()
		if len(args) >= 2 {
			keyPath = args[1]
		}
		if keyPath == "" {
			logf(true, "No signing key found. Pass one as an argument or as --key=<key.pem>, "+
				"or put one at %s", defaultKeyLocation())
		}
		key, err := loadSigningKey(keyPath)
		logf(err, "Failed to read %s", keyPath)
		fmt.Println(publicKeyHex(&key.PublicKey))

	case "cert":
		// cert [<leafPubHex>] -- issue a certificate with the signing key as
		// root. With no argument it self-signs (simple mode: the root
		// authorises its own key); with one it delegates to that leaf public
		// key. Prints the 264-hex-char certificate on stdout, which is
		// exactly what cdc/ether put on the wire.
		keyPath := findSigningKey()
		if keyPath == "" {
			logf(true, "No signing key found. Pass --key=<key.pem>, put one at %s, "+
				"or set \"signing_key\" in local_config.json", defaultKeyLocation())
		}
		leafPubHex := ""
		if len(args) >= 2 {
			leafPubHex = args[1]
		}
		certHex, err := issueLeafCert(keyPath, leafPubHex)
		logf(err, "Failed to issue certificate")
		fmt.Println(certHex)

	case "signraw":
		// signraw <hex> [<key.pem>] -- raw r||s signature over SHA-256(hex).
		// For the bootloader's owner-record chain, where the thing being
		// signed is a record prefix rather than a firmware image.
		if len(args) < 2 {
			logf(true, usageText)
		}
		keyPath := findSigningKey()
		if len(args) >= 3 {
			keyPath = args[2]
		}
		if keyPath == "" {
			logf(true, "No signing key found. Pass one as an argument or as --key=<key.pem>")
		}
		sig, err := signRawHex(args[1], keyPath)
		logf(err, "Failed to sign")
		fmt.Println(sig)

	case "getowner":
		if len(args) < 2 {
			logf(true, usageText)
		}
		RunGetOwner(args[1])

	case "takeown":
		if len(args) < 2 {
			logf(true, usageText)
		}
		takeownKey := ""
		if g_signing.keyExplicit {
			takeownKey = g_signing.keyPath
		}
		RunTakeOwn(args[1], takeownKey)

	case "setowner":
		if len(args) < 2 {
			logf(true, usageText)
		}
		RunSetOwner(args[1], g_signing.currentKey, g_signing.newKey)

	case ModeCDC:
		if len(args) < 3 {
			logf(true, usageText)
		}
		defer AcquireUploadLock()()
		RunCDC(args[2], args[1])

	case ModeEther:
		if len(args) < 3 {
			logf(true, usageText)
		}
		defer AcquireUploadLock()()
		RunEtherUpgrade(args[1], args[2])

	default:
		logf(true, "Invalid mode: %s. Use 'cdc', 'ether', 'sign', 'genkey', 'pubkey', "+
			"'cert', 'signraw', 'getowner', 'takeown' or 'setowner'", mode)
	}
}

// parseSigningFlags strips the --key / --cert / --out options out of the
// argument list and returns the remaining positional arguments. Both
// "--key=path" and "--key path" are accepted, in any position.
func parseSigningFlags(args []string) ([]string, error) {
	var positional []string

	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "--") {
			positional = append(positional, arg)
			continue
		}

		name, value, hasValue := strings.Cut(strings.TrimPrefix(arg, "--"), "=")
		if !hasValue {
			if i+1 >= len(args) {
				return nil, fmt.Errorf("option --%s needs a value", name)
			}
			i++
			value = args[i]
		}

		switch name {
		case "key":
			g_signing.keyPath = value
			g_signing.keyExplicit = true
		case "cert":
			g_signing.certPath = value
		case "out":
			g_signing.outPrefix = value
		case "current-key":
			g_signing.currentKey = value
		case "new-key":
			g_signing.newKey = value
		default:
			return nil, fmt.Errorf("unknown option --%s", name)
		}
	}

	return positional, nil
}
