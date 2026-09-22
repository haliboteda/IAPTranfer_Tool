package main

import (
	"fmt"
	"io"
	"net"
	"sort"
	"strings"
	"time"

	"IAPTool/iapcert"
	"IAPTool/internal/netiface"
)

// UDP

const (
	deviceReplyPrefix = "STM32H743"
)

type boardInfo struct {
	UID  string
	IP   string
	Role string
	// Version is the board package release (identity field 4) -- the same for
	// every sketch built with one package. AppVersion is the sketch's own
	// (field 5), which is what the version gate compares. Firmware older than
	// 2026-09-21 sends four fields and leaves AppVersion empty.
	Version    string
	AppVersion string
	Raw        string
}

const bootloaderDiscoveryRetries = 3

// How many times to ask a running application to reboot into the bootloader.
//
// The handshake is three UDP datagrams with no acknowledgement of its own --
// challenge out, nonce back, signed reboot out -- and until 2026-09-22 it got
// exactly one try, while the identify step next to it got three. A few percent
// of loss is therefore almost invisible on identify and lands squarely on this,
// which is what made "the reboot works sometimes" look like a certificate
// problem for weeks (OWN-08).
//
// Asking twice is harmless: a board that already rebooted is in its bootloader,
// which has no reboot command and ignores the datagram.
const rebootAttempts = 3

// The device replies to a given source at most once per DISCOVERY_MIN_REPLY_INTERVAL_MS
// (2s, see IAPServer/udp_server.c). Retrying sooner than that would be answered
// with the same silence, so the wait has to clear that window to be worth anything.
const identifyRetryDelay = 2500 * time.Millisecond

// RunEtherUpgrade sends an application image. RunEtherFlashBoot sends a
// bootloader image over the same path -- see $PROD/docs/modules/M1/FLASHBOOT.md
// for what the board does differently with it.
func RunEtherUpgrade(filePath, ip string) {
	runEtherFlow(filePath, ip, CM_Flash)
}

// RunEtherFlashBoot replaces the board's bootloader.
//
// The key it signs with has to be the owner root itself: the board checks a
// bootloader image against the root, not against the certificate's leaf. The
// same key also serves as the session identity, so one --key is enough.
func RunEtherFlashBoot(filePath, ip string) {
	logf("** flashboot replaces the bootloader in place. Do not cut power. **")
	runEtherFlow(filePath, ip, CM_FlashBoot)
}

func runEtherFlow(filePath, ip, verb string) {
	logf("[PATH] start ether %s flow, target=%s", verb, ip)

	resp, err := udpIdentifyWithRetry(ip)
	if err != nil {
		logf(true, "No response from %s after %d attempts, exiting.", ip, bootloaderDiscoveryRetries)
		return
	}

	board, ok := parseBoardInfoFromReply(resp, ip)
	if !ok {
		logf(true, "Unrecognized reply from %s: %q, exiting.", ip, resp)
		return
	}

	targetUID := board.UID
	logf("[PATH] target device UID=%s cached for this upgrade", targetUID)

	// Has to run here, before anything asks the board to leave the
	// application: in the bootloader the app-version field is "-" and what is
	// currently installed can no longer be read. A board that never answered
	// has already exited above, so reaching this line means discovery worked.
	// Skipped for flashboot: a bootloader image carries no sketch version,
	// and the field the gate reads describes the application.
	if verb == CM_Flash {
		checkVersionGate(filePath, board, true, g_forceFlash)
	}

	// Resolved once and reused: a run that starts from the application state
	// authenticates twice (the reboot, then the flash), and issuing a fresh
	// self-signed certificate for each would burn two serial numbers on one
	// upload.
	id, err := resolveUploadIdentity()
	if err != nil {
		logf(err, "Cannot authenticate to this board")
		return
	}

	switch strings.ToUpper(board.Role) {
	case "BOOTLD-INVALID":
		logf("[PATH] %s is bootloader with NO valid signed app installed (previous update failed, was rejected, or flash was tampered with) -> proceeding to flash a new image", ip)
		RunEther_TCP(filePath, ip, id, verb, targetUID)

	case "BOOTLD":
		logf("[PATH] %s is bootloader -> tcp transfer", ip)
		RunEther_TCP(filePath, ip, id, verb, targetUID)

	case "CUSAPP":
		logf("[PATH] %s is app -> reboot to bootloader", ip)
		// Ask, wait, look -- and ask again if it is still running. Nothing in
		// this handshake is acknowledged, so a lost datagram is only ever
		// visible as "the board did not come back".
		var bootBoard boardInfo
		var ok bool
		for attempt := 1; attempt <= rebootAttempts; attempt++ {
			if err := authenticatedUDPReboot(ip, id); err != nil {
				logf("Reboot request %d/%d did not get through: %v",
					attempt, rebootAttempts, err)
				continue
			}

			wait := getRebootWaitDuration()
			logf("Waiting %.1f seconds for reboot...", wait.Seconds())
			time.Sleep(wait)

			if bootBoard, ok = discoverBootloader(bootloaderDiscoveryRetries, targetUID); ok {
				break
			}
			if attempt < rebootAttempts {
				logf("%s is still running its application; asking it to reboot again (%d/%d)",
					ip, attempt+1, rebootAttempts)
			}
		}
		if !ok {
			logf(true, "No bootloader with UID=%s found after %d reboot request(s), exiting.",
				targetUID, rebootAttempts)
			return
		}
		logf("[PATH] bootloader found at %s (uid=%s) -> tcp transfer", bootBoard.IP, bootBoard.UID)
		RunEther_TCP(filePath, bootBoard.IP, id, verb, targetUID)

	default:
		logf(true, "Unexpected role %q from %s, exiting.", board.Role, ip)
	}
}

// discoverBootloader repeats the broadcast discovery until a bootloader reply
// carrying targetUID is seen, since multiple devices on the LAN may answer the
// broadcast and only the one that was just rebooted should be flashed.
func discoverBootloader(maxAttempts int, targetUID string) (boardInfo, bool) {
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		boards, err := discoverBoardsViaDirectedBroadcast("BOOTLD")
		if err != nil {
			logf("Bootloader discovery attempt %d/%d: %v", attempt, maxAttempts, err)
			continue
		}
		if board, ok := selectDiscoveredBoard(boards, targetUID); ok {
			return board, true
		}
		logf("Bootloader discovery attempt %d/%d: target UID=%s not among %d discovered device(s)", attempt, maxAttempts, targetUID, len(boards))
	}
	return boardInfo{}, false
}

func discoverBoardsViaDirectedBroadcast(expectedRole string) ([]boardInfo, error) {
	broadcastAddrs, err := getDirectedBroadcastAddrs()
	if err != nil {
		return nil, err
	}
	if len(broadcastAddrs) == 0 {
		return nil, fmt.Errorf("no broadcast address found from local interfaces")
	}

	localAddr := &net.UDPAddr{IP: net.IPv4zero, Port: 0}
	conn, err := net.ListenUDP("udp4", localAddr)
	if err != nil {
		return nil, fmt.Errorf("UDP listen failed: %w", err)
	}
	defer conn.Close()

	deadline := time.Now().Add(Timeout)
	conn.SetDeadline(deadline)

	for _, bcast := range broadcastAddrs {
		remoteAddr, rerr := net.ResolveUDPAddr("udp4", bcast+":"+getPort())
		if rerr != nil {
			logf("Skip invalid broadcast addr %s: %v", bcast, rerr)
			continue
		}
		if _, werr := conn.WriteToUDP([]byte(CM_PullIP), remoteAddr); werr != nil {
			logf("Broadcast send failed to %s: %v", remoteAddr.String(), werr)
			continue
		}
		logf("UDP broadcast sent to %s", remoteAddr.String())
	}

	var boards []boardInfo
	buffer := make([]byte, Buf_s)
	for {
		n, addr, rerr := conn.ReadFromUDP(buffer)
		if rerr != nil {
			if ne, ok := rerr.(net.Error); ok && ne.Timeout() {
				break
			}
			logf("UDP receive error: %v", rerr)
			continue
		}

		info, ok := parseBoardInfoFromReply(string(buffer[:n]), addr.IP.String())
		if !ok {
			logf("Ignore invalid discovery response from %s: %q", addr.IP.String(), strings.TrimSpace(string(buffer[:n])))
			continue
		}
		// An empty expectedRole means "whatever it is now": used to confirm a
		// board came back after a write, when which role it lands in is the
		// thing that depends on what was written.
		if expectedRole != "" && !strings.EqualFold(info.Role, expectedRole) {
			logf("Ignore %s response from %s while waiting for %s: %s", info.Role, info.IP, expectedRole, info.Raw)
			continue
		}
		boards = append(boards, info)
	}

	if len(boards) == 0 {
		return nil, fmt.Errorf("no valid %s discovery response within %.1f seconds", expectedRole, Timeout.Seconds())
	}
	printDiscoveredBoards(boards, expectedRole)
	return boards, nil
}

func parseBoardInfoFromReply(reply, fallbackIP string) (boardInfo, bool) {
	raw := strings.TrimSpace(reply)
	parts := strings.Split(raw, "_")
	if len(parts) < 4 {
		return boardInfo{}, false
	}
	if strings.TrimSpace(parts[0]) != deviceReplyPrefix {
		return boardInfo{}, false
	}

	uid := strings.TrimSpace(parts[1])
	role := strings.ToUpper(strings.TrimSpace(parts[2]))
	ip := strings.TrimSpace(fallbackIP)
	if uid == "" || ip == "" || role == "" {
		return boardInfo{}, false
	}

	appVersion := ""
	if len(parts) >= 5 {
		appVersion = strings.TrimSpace(parts[4])
	}

	return boardInfo{
		UID:        uid,
		IP:         ip,
		Role:       role,
		Version:    strings.TrimSpace(parts[3]),
		AppVersion: appVersion,
		Raw:        raw,
	}, true
}

func printDiscoveredBoards(boards []boardInfo, expectedRole string) {
	logf("Discovered %d %s device(s) in %.1f seconds:", len(boards), expectedRole, Timeout.Seconds())
	for i, b := range boards {
		fmt.Printf("  [%d] UID=%s IP=%s Role=%s Reply=%s\n", i+1, b.UID, b.IP, b.Role, b.Raw)
	}
	fmt.Printf("Default selection: [1]")
	if expectedRole == "CUSAPP" {
		fmt.Printf(" (you can modify local_config.json appIP if you want a different device)")
	}
	fmt.Printf("\n")
}

func selectDiscoveredBoard(boards []boardInfo, targetUID string) (boardInfo, bool) {
	for _, b := range boards {
		if strings.EqualFold(b.UID, targetUID) {
			logf("Matched cached target device: uid=%s ip=%s", b.UID, b.IP)
			return b, true
		}
	}
	return boardInfo{}, false
}

// udpIdentifyWithRetry asks a known address who it is, retrying like the
// broadcast discovery already does. A single lost datagram must not end the
// upgrade: UDP has no delivery guarantee, and the operator only ever sees
// "no response", which reads as a dead board rather than a dropped packet.
func udpIdentifyWithRetry(ip string) (string, error) {
	var lastErr error

	for attempt := 1; attempt <= bootloaderDiscoveryRetries; attempt++ {
		resp, err := udpPingAndGetStatus(ip)
		if err == nil {
			return resp, nil
		}
		lastErr = err
		if attempt < bootloaderDiscoveryRetries {
			logf("Identify attempt %d/%d on %s got no usable reply (%v), retrying in %.1fs...",
				attempt, bootloaderDiscoveryRetries, ip, err, identifyRetryDelay.Seconds())
			time.Sleep(identifyRetryDelay)
		}
	}
	return "", lastErr
}

func udpPingAndGetStatus(ip string) (string, error) {
	// CM_PullIP, not CM_Ping: "ping" answers with the identity string over UDP but
	// with "OK" over CDC/TCP, and one command must not mean two things.
	buffer, _, err := sendUDPWithResponseOnPort(ip, getPort(), CM_PullIP, CommandTimeout)
	if err != nil {
		return "", err
	}

	resp := strings.TrimSpace(string(buffer))
	if !strings.HasPrefix(resp, deviceReplyPrefix) {
		return "", fmt.Errorf("unexpected UDP ping response: %q", resp)
	}
	return resp, nil
}

// Send UDP message and wait for a response within timeout.
// Returns response bytes and error (nil if success).
func sendUDPWithResponseOnPort(serverAddr, port, msg string, timeout time.Duration) ([]byte, *net.UDPAddr, error) {
	conn, err := dialUDPBoard(serverAddr, port)
	if err != nil {
		return nil, nil, err
	}
	defer conn.Close()

	conn.SetDeadline(time.Now().Add(timeout))

	if _, err := conn.Write([]byte(msg)); err != nil {
		return nil, nil, fmt.Errorf("UDP Write failed: %w", err)
	}
	logf("UDP sent: %s", msg)

	// A connected socket only accepts datagrams from the address it dialed, so
	// this can no longer be answered by some other device on the network.
	buffer := make([]byte, Buf_s)
	n, err := conn.Read(buffer)
	if err != nil {
		return nil, nil, fmt.Errorf("UDP Read Timeout: %w", err)
	}

	return buffer[:n], conn.RemoteAddr().(*net.UDPAddr), nil
}

// authenticatedUDPReboot performs the challenge-response handshake before
// sending CM_Reboot: request a nonce, sign the exact reboot command, then send
// the certificate and that signature. An unauthenticated
// "openplc_server_reboot" is ignored by the device.
func authenticatedUDPReboot(ip string, id uploadIdentity) error {
	nonceResp, _, err := sendUDPWithResponseOnPort(ip, getPort(), CM_RebootChallenge, Timeout)
	if err != nil {
		return fmt.Errorf("reboot challenge request failed: %w", err)
	}
	noncesigHex, err := iapcert.NonceSig(id.key, string(nonceResp), CM_Reboot)
	if err != nil {
		return err
	}
	return sendUDPNoResponseOnPort(ip, getPort(), fmt.Sprintf("%s %s %s", CM_Reboot, id.certHex, noncesigHex))
}

// Send UDP message without waiting for a response.
func sendUDPNoResponseOnPort(serverAddr, port, msg string) error {
	conn, err := dialUDPBoard(serverAddr, port)
	if err != nil {
		return err
	}
	defer conn.Close()

	_, err = conn.Write([]byte(msg))
	if err != nil {
		return fmt.Errorf("UDP send failed: %w", err)
	}

	return nil
}

// dialUDPBoard opens a UDP connection to the board with the source address
// pinned to the physical interface on the board's subnet, for every unicast
// exchange in this file: identify, reboot challenge, and the authenticated
// reboot command. Until 2026-09-18 only the broadcast discovery function had
// this pin (decision 51); these four calls are the ones a real upload
// actually hits every single time, so an unpinned socket here is worse, not
// better, than the broadcast case -- a VPN with a better-metric default route
// can take the packet even though the user already gave a specific IP, and on
// Windows a VPN endpoint has been observed to complete the connection and then
// reset it, which is why a connected socket (not a bare listen) is used here:
// it only accepts replies from the address it dialed.
//
// netiface.LocalIPFor and its platform classifiers already cover Windows,
// Linux and macOS (build-tag gated in internal/netiface/iface_*.go); nothing
// platform-specific is added here.
//
// When no physical interface shares the board's subnet, the board is reached
// through a router, so the plain dial (nil local address) is correct.
func dialUDPBoard(serverAddr, port string) (*net.UDPConn, error) {
	raddr, err := net.ResolveUDPAddr("udp4", serverAddr+":"+port)
	if err != nil {
		return nil, fmt.Errorf("UDP ResolveUDPAddr failed: %w", err)
	}
	var laddr *net.UDPAddr
	if local := netiface.LocalIPFor(raddr.IP); local != nil {
		laddr = &net.UDPAddr{IP: local}
	}
	conn, err := net.DialUDP("udp4", laddr, raddr)
	if err != nil {
		return nil, fmt.Errorf("UDP connection failed: %w", err)
	}
	return conn, nil
}

// dialTCPBoard is dialUDPBoard's sibling for the flash channel: RunEther_TCP
// dials it once for the whole upload, and an unpinned TCP dial to a specific
// IP is exactly what let a VPN endpoint answer for the board on 2026-09-18
// (measured: connect completed in 0.03s, then reset, from a host that was
// never on the board's subnet).
func dialTCPBoard(serverIP string) (net.Conn, error) {
	target := serverIP + ":" + getPort()
	raddr, err := net.ResolveTCPAddr("tcp", target)
	if err == nil {
		if local := netiface.LocalIPFor(raddr.IP); local != nil {
			d := net.Dialer{LocalAddr: &net.TCPAddr{IP: local}, Timeout: Timeout}
			return d.Dial("tcp", target)
		}
	}
	return net.DialTimeout("tcp", target, Timeout)
}

// Only physical interfaces are broadcast to. A VPN tunnel or a Docker switch
// answers nothing and its broadcast address merely costs a timeout, while its
// default route can pull the probe off the real NIC entirely. See
// $PROD/docs/tables/DECISIONS.md decision 51.
func getDirectedBroadcastAddrs() ([]string, error) {
	ifaces, err := netiface.Physical()
	if err != nil {
		return nil, err
	}

	seen := make(map[string]struct{})
	var addrs []string
	for _, iface := range ifaces {
		ifaceAddrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range ifaceAddrs {
			ipNet, ok := addr.(*net.IPNet)
			if !ok || ipNet.IP == nil {
				continue
			}
			ip4 := ipNet.IP.To4()
			if ip4 == nil || len(ipNet.Mask) != net.IPv4len {
				continue
			}

			bcast := net.IPv4(
				ip4[0]|^ipNet.Mask[0],
				ip4[1]|^ipNet.Mask[1],
				ip4[2]|^ipNet.Mask[2],
				ip4[3]|^ipNet.Mask[3],
			).String()
			if _, ok := seen[bcast]; ok {
				continue
			}
			seen[bcast] = struct{}{}
			addrs = append(addrs, bcast)
		}
	}
	sort.Strings(addrs)
	return addrs, nil
}

// TCP
//
// One connection for the whole upload: dial, ping, confirm the board accepts
// this identity, then send the file. An earlier version opened a second,
// separate connection for the identity check because that check used to also
// confirm a downgrade with the operator, and the board drops idle connections
// while a human is being asked something. That confirmation is gone (see
// DECISIONS.md decision 54); the identity check is now a plain round trip
// with nothing to wait on, so there is no longer a reason to split it off.
func RunEther_TCP(filePath, serverIP string, id uploadIdentity, verb, targetUID string) {
	sigHex, err := signImageInMemory(filePath, id.key)
	logf(err, "Failed to prepare signature for %s", filePath)

	logf("Trying to connect to TCP server at %s...", serverIP)

	conn, err := dialTCPBoard(serverIP)
	if err != nil {
		logf(true, "Failed to connect to server: %v", err)
	}
	defer conn.Close()

	if err := ping(conn); err != nil {
		logf(true, "Ping failed: %v", err)
	}

	if err := verifyIdentityMatchesDevice(id, func() (string, error) {
		return sendAndReadResponse(conn, []byte(CM_GetPubKey+"\n"))
	}); err != nil {
		logf(true, "Signing key check failed: %v", err)
	}

	if err := sendFile(conn, filePath, id, sigHex, verb, targetUID); err != nil {
		logf(err, "File send failed")
	}
}

// getPort returns the configured server port (shared by the TCP flash
// channel and the UDP discovery/reboot channel), falling back to the default.
func getPort() string {
	if strings.TrimSpace(l_config.ServerPort) != "" {
		return strings.TrimSpace(l_config.ServerPort)
	}
	return defaultServerPort
}

func getRebootWaitDuration() time.Duration {
	if l_config.RebootWaitSeconds > 0 {
		return time.Duration(l_config.RebootWaitSeconds) * time.Second
	}
	return time.Duration(defaultRebootWaitSeconds) * time.Second
}

// 发送 ping 并等待 ok
func ping(conn net.Conn) error {
	var lastErr error
	for attempt := 1; attempt <= MaxRetries; attempt++ {
		logf("Sending ping...")
		if err := sendAndWaitOK(conn, []byte(CM_Ping+"\n")); err != nil {
			logf("ping failed: %v", err)
			lastErr = err
		} else {
			logf("Ping successful.")
			return nil
		}

		time.Sleep(1 * time.Second)
	}
	if lastErr != nil {
		return fmt.Errorf("ping failed after %d retries: %w", MaxRetries, lastErr)
	}
	return fmt.Errorf("ping failed after %d retries", MaxRetries)
}

// 文件发送函数（按 buffer 分块发送，每块等 ok）
// sendFile carries no interactive step: the identity check already ran on
// this same connection, in RunEther_TCP, before this is called.
func sendFile(conn net.Conn, filePath string, id uploadIdentity, sigHex, verb, targetUID string) error {
	// Calculate checksum and file size
	checksum, fileSize, file := CalculateCRC32(filePath)
	defer file.(io.Closer).Close()
	logf("CRC Checksum: %x", checksum)

	authMsg := fmt.Sprintf("%s %d %x %s", verb, fileSize, checksum, sigHex)

	nonceResp, err := sendAndReadResponse(conn, []byte(CM_AuthChallenge+"\n"))
	if err != nil {
		return fmt.Errorf("auth challenge failed: %v", err)
	}
	noncesigHex, err := iapcert.NonceSig(id.key, nonceResp, authMsg)
	if err != nil {
		return err
	}

	// Send flash command
	flashCmd := fmt.Sprintf("%s %s %s", authMsg, id.certHex, noncesigHex)
	if err := sendAndWaitOK(conn, []byte(flashCmd+"\n")); err != nil {
		return fmt.Errorf("failed to send FLASH: %v", err)
	}
	// Raw binary chunks below -- do NOT append "\n" framing to these, only
	// to the text commands above; the device switches to FLASH_RECEIVE
	// state after the "OK" ack and treats every subsequent byte as image
	// data, not text to scan for a newline.
	buf := make([]byte, Buf_b)
	for {
		n, readErr := file.Read(buf)
		if n > 0 {
			if err := sendAndWaitOK(conn, buf[:n]); err != nil {
				return fmt.Errorf("failed to send chunk: %v", err)
			}
		}
		if readErr == io.EOF {
			// The board has every byte now, but it has not judged them yet:
			// it verifies the staged image and only then says whether it
			// took it. Reading that verdict is the difference between
			// "uploaded" and "accepted" -- without it a refused image was
			// reported as a successful transfer.
			if err := readFinalVerdict(conn, verb, targetUID); err != nil {
				return err
			}
			logf("File transfer complete.")
			// Only now is the one-shot force marker written: a transfer that
			// failed above never reaches this line, so retrying a failed
			// forced flash is not blocked.
			noteFlashSucceeded()
			break
		}
		if readErr != nil {
			return fmt.Errorf("file read error: %v", readErr)
		}
	}
	return nil
}

// After the last chunk the board verifies the image, and only then decides.
// It answers ONLY to refuse -- "Signature Failed", "No Signature",
// "Flash Failed". Both success paths reset the board instead, and a reset
// takes the MAC down before anything queued for the peer leaves it, so there
// is no "OK" to wait for and there cannot be one (measured 2026-09-22).
//
// So success is confirmed the only way it can be: the board comes back and
// answers discovery again.
const (
	// The whole window. Long because a refusal from the flash write itself
	// arrives only after the application region has been erased and written.
	verdictTimeout = 90 * time.Second
	// One slice of TCP listening before looking for the board on the network
	// instead. Short: a refusal that is coming is already on its way.
	verdictPoll = 2 * time.Second
)

// readFinalVerdict decides whether the board took the image.
//
// Returns nil only on evidence: either the board said OK (no path does today,
// but a refusal-shaped silence must not be read as one), or it came back on
// the network, which only happens after it reset, which only happens when it
// accepted. Anything the board says other than OK is a refusal.
func readFinalVerdict(conn net.Conn, verb, targetUID string) error {
	deadline := time.Now().Add(verdictTimeout)

	for time.Now().Before(deadline) {
		// Errors here are not failures: a reset board yields a timeout or a
		// dropped connection, and that is one of the expected outcomes.
		resp, _ := readWithIdleGap(conn, verdictPoll)
		if said := strings.TrimSpace(string(resp)); said != "" {
			if said == Rsp_OK {
				return nil
			}
			return fmt.Errorf("the board refused the image: %s", said)
		}
		if boardCameBack(targetUID) {
			logf("The board reset and is answering again - the image was accepted.")
			return nil
		}
	}
	return fmt.Errorf("the board neither refused the image nor came back within %s. "+
		"It was NOT confirmed: check its log before assuming anything was written", verdictTimeout)
}

// boardCameBack reports whether the target is answering discovery again, in
// whatever role the thing just written leaves it in.
func boardCameBack(targetUID string) bool {
	if targetUID == "" {
		return false
	}
	boards, err := discoverBoardsViaDirectedBroadcast("")
	if err != nil {
		return false
	}
	_, ok := selectDiscoveredBoard(boards, targetUID)
	return ok
}

// readWithIdleGap accumulates reads from conn until it goes quiet for
// idleGap, instead of trusting a single Read() to return a complete reply.
// Mirrors the CDC-side fix in SendCommandReadResponse: a short TCP write on
// the device side can still reach the caller split across more than one
// Read() (Go's net.Conn makes no promise that one Write() on the far end
// arrives as one Read() on this end), so treating the first Read() as "the
// whole response" is not safe here either.
func readWithIdleGap(conn net.Conn, overallTimeout time.Duration) ([]byte, error) {
	const idleGap = 100 * time.Millisecond
	buf := make([]byte, 256)
	var accumulated []byte
	deadline := time.Now().Add(overallTimeout)

	for time.Now().Before(deadline) {
		conn.SetReadDeadline(time.Now().Add(idleGap))
		n, err := conn.Read(buf)
		if n > 0 {
			accumulated = append(accumulated, buf[:n]...)
			continue
		}
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				if len(accumulated) > 0 {
					return accumulated, nil
				}
				continue
			}
			return accumulated, err
		}
	}
	if len(accumulated) == 0 {
		return nil, fmt.Errorf("timeout waiting for response")
	}
	return accumulated, nil
}

// 通用函数：发送数据，等待 server 回复 "ok"
func sendAndWaitOK(conn net.Conn, data []byte) error {
	conn.SetWriteDeadline(time.Now().Add(Timeout))
	dataLen, err := conn.Write(data)
	if err != nil {
		return fmt.Errorf("send error: %v", err)
	}

	logf("Sent %d bytes", dataLen)

	resp, err := readWithIdleGap(conn, Timeout)
	if err != nil {
		return fmt.Errorf("read ack error: %v", err)
	}
	if strings.TrimSpace(string(resp)) != Rsp_OK {
		return fmt.Errorf("unexpected ack: %s", string(resp))
	}
	return nil
}

// sendAndReadResponse sends data and returns whatever the device replies
// with (trimmed), rather than checking against a fixed "OK". Used to read
// back the nonce from an "authchallenge" request.
func sendAndReadResponse(conn net.Conn, data []byte) (string, error) {
	conn.SetWriteDeadline(time.Now().Add(Timeout))
	if _, err := conn.Write(data); err != nil {
		return "", fmt.Errorf("send error: %v", err)
	}
	logf("Sent %d bytes", len(data))

	resp, err := readWithIdleGap(conn, Timeout)
	if err != nil {
		return "", fmt.Errorf("read response error: %v", err)
	}
	return strings.TrimSpace(string(resp)), nil
}
