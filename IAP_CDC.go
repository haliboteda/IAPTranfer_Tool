package main

import (
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"go.bug.st/serial"
)

// Helper function to open a serial port with specified baud rate
func openPort(comName string, baudRate int) (serial.Port, error) {
	mode := &serial.Mode{BaudRate: baudRate}
	port, err := serial.Open(comName, mode)
	if err != nil {
		return nil, err
	}
	if port == nil {
		return nil, errors.New("serial.Open returned nil port")
	}
	return port, nil
}

// Retry logic for opening serial port
func retryOpenPort(comName string, baudRate int, maxRetries int) serial.Port {
	var port serial.Port
	var err error
	for attempt := 1; attempt <= maxRetries; attempt++ {
		time.Sleep(2 * time.Second)
		port, err = openPort(comName, baudRate)
		if err == nil {
			logf("Successfully opened port on attempt %d", attempt)
			return port
		}
		logf("Failed to open port %s on attempt %d of %d. Retrying in 2 seconds...", comName, attempt, maxRetries)

	}
	logf(true, "Failed to open serial port %s after %d attempts", comName, maxRetries)
	return nil
}

func RunCDC(portName, filePath string) {
	logf("[PATH] start cdc upgrade flow, target=%s", portName)

	board, ok := cdcIdentify(portName)
	if !ok {
		logf("No bootloader answered. Asking the application to reboot into it...")
		triggerPortResetAndWait(portName, getRebootWaitDuration())

		for attempt := 1; attempt <= bootloaderDiscoveryRetries; attempt++ {
			if board, ok = cdcIdentify(portName); ok {
				break
			}
			logf("Bootloader identify attempt %d/%d on %s found nothing", attempt, bootloaderDiscoveryRetries, portName)
			time.Sleep(time.Second)
		}
		if !ok {
			logf(true, "No bootloader on %s after the reboot request, exiting.", portName)
		}
	}

	switch board.Role {
	case "BOOTLD-INVALID":
		logf("[PATH] %s is bootloader with NO valid signed app installed (previous update failed, was rejected, or flash was tampered with) -> proceeding to flash a new image", portName)
	case "BOOTLD":
		logf("[PATH] %s is bootloader -> cdc transfer", portName)
	default:
		logf(true, "Unexpected role %q from %s, exiting.", board.Role, portName)
	}

	/* The COM port name is not an identity: the board re-enumerates after the
	 * reboot request, and on a bench with several boards the name can come back
	 * pointing at a different one. Refuse rather than flash a stranger. */
	if want := strings.TrimSpace(l_config.UID); want != "" && !strings.EqualFold(want, board.UID) {
		logf(true, "Device on %s reports uid=%s, not the configured target uid=%s. Refusing to flash.",
			portName, board.UID, want)
	}
	logf("[PATH] target device UID=%s", board.UID)

	runCDCAttempt(portName, filePath, board.UID)
}

// cdcIdentify asks the port who it is, using the same identity string the UDP
// discovery reply carries. A board running the user's application never answers:
// the sketch owns the CDC data pipe, so silence -- or anything the sketch echoes
// back -- is what "the application is running" looks like from here.
func cdcIdentify(portName string) (boardInfo, bool) {
	port, err := openPort(portName, l_config.BaudRate)
	if err != nil {
		logf("Failed to open serial port %s with baud rate %d: %v", portName, l_config.BaudRate, err)
		return boardInfo{}, false
	}
	defer port.Close()

	reply, err := SendCommandReadResponse(port, CM_PullIP, CommandTimeout)
	if err != nil {
		return boardInfo{}, false
	}
	return parseBoardInfoFromReply(reply, portName)
}

// The application reboots the moment it sees the port opened at MagicBaud, so
// the open itself usually fails with the port already gone. That is the normal
// outcome, not an error: a board that did not take the reset is reported by the
// ping that follows.
func triggerPortResetAndWait(portName string, wait time.Duration) {
	if port, err := openPort(portName, MagicBaud); err == nil {
		port.Close()
	}
	logf("Reboot requested on %s@%d. Waiting %.1f seconds for the bootloader...",
		portName, MagicBaud, wait.Seconds())
	time.Sleep(wait)
	logf("Reconnecting with default baud now.")
}

// uidHex comes from the identity reply, so no separate getuid round trip.
func runCDCAttempt(portName, filePath, uidHex string) {
	port, err := openPort(portName, l_config.BaudRate)
	if err != nil {
		logf(true, "Failed to open serial port %s with baud rate %d: %v", portName, l_config.BaudRate, err)
	}
	defer port.Close()

	deviceKey, err := deriveDeviceKeyFromUIDHex(uidHex)
	if err != nil {
		logf(err, "Failed to derive device key")
	}

	logf("Proceeding with file transfer.")
	checksum, fileSize, file := CalculateCRC32(filePath)
	defer file.(io.Closer).Close()
	logf("CRC Checksum: %x", checksum)

	auth, err := resolveImageAuth(filePath)
	if err != nil {
		logf(err, "Failed to prepare signature for %s", filePath)
		return
	}

	if err := verifyKeyMatchesDevice(auth, func() (string, error) {
		return SendCommandReadResponse(port, CM_GetPubKey, CommandTimeout)
	}); err != nil {
		logf(err, "Signing key check failed")
		return
	}

	sigHex, localVersion, haveVersion := auth.sigHex, auth.version, auth.haveVersion

	if haveVersion {
		remoteVer, verErr := SendCommandReadResponse(port, CM_GetVersion, CommandTimeout)
		if verErr != nil {
			logf("Could not query installed version (older bootloader?): %v -- skipping downgrade check", verErr)
		} else if !confirmDowngradeIfNeeded(localVersion, remoteVer) {
			logf("Downgrade declined by operator. Aborting.")
			return
		}
	}

	base := fmt.Sprintf("%s %d %x %s", CM_Flash, fileSize, checksum, sigHex)
	authMsg := base
	if haveVersion {
		authMsg = fmt.Sprintf("%s %d", base, localVersion)
	}

	nonceResp, err := SendCommandReadResponse(port, CM_AuthChallenge, CommandTimeout)
	if err != nil {
		logf(err, "Auth challenge failed")
		return
	}
	hmacHex, err := computeAuthHMAC(deviceKey, nonceResp, authMsg)
	if err != nil {
		logf(err, "Failed to compute auth HMAC")
		return
	}

	flashCmd := fmt.Sprintf("%s %s", base, hmacHex)
	if haveVersion {
		flashCmd = fmt.Sprintf("%s %s %d", base, hmacHex, localVersion)
	}
	if !SendCommandWaitForResponse(port, flashCmd, Rsp_OK, FlashAckTimeout) {
		return
	}

	err = SendFile(port, file, fileSize)
	logf(err, "File transfer failed")
}

// SendCommandWaitForResponse sends a command through the serial port,
// waits for a specific response string, and returns true if received within timeout.
//
// The trailing "\n" frames the command so the bootloader knows where it
// ends -- without it, a command longer than one USB CDC packet (e.g.
// "flash ... <sig> <hmac>") can arrive split across multiple reads on the
// device side with no way to tell it's still the same command.
func SendCommandWaitForResponse(port serial.Port, command string, expected string, timeout time.Duration) bool {
	if _, err := port.Write([]byte(command + "\n")); err != nil {
		logf(err, "Failed to send command: %s", command)
		return false
	}
	logf("Send command: %s", command)

	return ReadResponse(port, expected, timeout)
}
