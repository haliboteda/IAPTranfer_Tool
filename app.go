package main

import (
	"fmt"
	"os"
	"strconv"
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
	PasswordFile      string `json:"password_file"`
}

var l_config LocalConfig

// What to do when the image is older than what the device already runs.
const (
	DowngradeAsk    = "ask"
	DowngradeAllow  = "allow"
	DowngradeRefuse = "refuse"
)

// signingOptions holds the command-line signing overrides for this run.
type signingOptions struct {
	keyPath      string
	passwordPath string
	outPrefix    string
	version      uint32
	haveVersion  bool
	downgrade    string
}

var g_signing = signingOptions{downgrade: DowngradeAsk}

const usageText = `Usage:
  IAPTool cdc    <file.bin> <port>       [--key=<key.pem>] [--version=N]
  IAPTool ether  <file.bin> <ip>         [--key=<key.pem>] [--version=N]
  IAPTool sign   <file.bin> [<key.pem>]  [--key=<key.pem>] [--version=N] [--out=<prefix>]
  IAPTool genkey [<name>]    writes <name>.pem, prints keys/fw_pubkey.inc on stdout
  IAPTool genpw              prints a fresh keys/iap_fixed_password.txt on stdout
  IAPTool version <x.y.z> [--out=<file>]  encodes a dotted version as the uint32 the
                   device compares, one byte per field. Prints it, or writes it to
                   <file> for the build to drop next to the image as <image>.version.

  --key            ECDSA P-256 private key (PEM). When omitted, falls back to
                   "signing_key" in local_config.json, then to keys/fw_signing_key.pem
                   next to this executable. The image is then signed in memory, so no
                   .sig file is needed. With no key anywhere, cdc/ether use the sibling
                   <file>.sig written by an earlier "sign" run.
  --password-file  Shared IAP password file. When omitted, falls back to
                   "password_file" in local_config.json, then to
                   keys/iap_fixed_password.txt next to this executable. It must hold
                   the same password compiled into the board's firmware.
  --version        Firmware version to flash. Defaults to the sibling <file>.version file.
  --downgrade      ask (default) / allow / refuse, when the image is older than the
                   one already on the device. "ask" needs a console: started from an
                   IDE there is nothing to type into, so it refuses instead.
  --out            Output prefix for "sign". Defaults to the .bin path without its extension.

To rotate both secrets at once, run IAPServer/keys/rotate_keys.sh.`

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
		err := signBinFile(args[1], keyPath, g_signing.outPrefix, g_signing.version, g_signing.haveVersion)
		logf(err, "Failed to sign %s", args[1])

	case "genkey":
		name := "fw_signing_key"
		if len(args) >= 2 {
			name = args[1]
		}
		err := generateSigningKey(name)
		logf(err, "Failed to generate signing key")

	case "version":
		if len(args) < 2 {
			logf(true, usageText)
		}
		encoded, err := encodeSemver(args[1])
		logf(err, "Cannot encode version %q", args[1])
		if out := strings.TrimSpace(g_signing.outPrefix); out != "" {
			err = os.WriteFile(out, []byte(strconv.FormatUint(uint64(encoded), 10)+"\n"), 0644)
			logf(err, "Failed to write %s", out)
			logf("Wrote %s: %s -> %d", out, args[1], encoded)
		} else {
			fmt.Println(encoded)
		}

	case "genpw":
		content, err := generatePasswordFile()
		logf(err, "Failed to generate password")
		fmt.Print(content)

	case ModeCDC:
		if len(args) < 3 {
			logf(true, usageText)
		}
		loadFixedPassword()
		RunCDC(args[2], args[1])

	case ModeEther:
		if len(args) < 3 {
			logf(true, usageText)
		}
		loadFixedPassword()
		RunEtherUpgrade(args[1], args[2])

	default:
		logf(true, "Invalid mode: %s. Use 'cdc', 'ether', 'sign', 'genkey' or 'genpw'", mode)
	}
}

// parseSigningFlags strips the --key / --out / --version options out of the
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
		case "password-file":
			g_signing.passwordPath = value
		case "out":
			g_signing.outPrefix = value
		case "version":
			parsed, err := strconv.ParseUint(strings.TrimSpace(value), 10, 32)
			if err != nil {
				return nil, fmt.Errorf("invalid --version %q: %w", value, err)
			}
			g_signing.version, g_signing.haveVersion = uint32(parsed), true
		case "downgrade":
			switch strings.ToLower(strings.TrimSpace(value)) {
			case DowngradeAsk, DowngradeAllow, DowngradeRefuse:
				g_signing.downgrade = strings.ToLower(strings.TrimSpace(value))
			default:
				return nil, fmt.Errorf("invalid --downgrade %q: use ask, allow or refuse", value)
			}
		default:
			return nil, fmt.Errorf("unknown option --%s", name)
		}
	}

	return positional, nil
}
