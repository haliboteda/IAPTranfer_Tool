package main

import (
	"fmt"
	"io"
	"net"
	"sort"
	"strings"
	"time"
)

// UDP

const (
	deviceReplyPrefix = "STM32H743"
)

type boardInfo struct {
	UID     string
	IP      string
	Role    string
	Version string
	Raw     string
}

func RunEtherUpgrade(filePath string) {
	logf("[PATH] start ether upgrade flow")

	for attempt := 1; attempt <= 3; attempt++ {
		if bootBoard, ok := ensureBootBoard(); ok {
			logf("[PATH] bootloader ready -> tcp transfer")
			RunEther_TCP(filePath, bootBoard.IP)
			return
		}

		appBoard, ok := ensureAppBoard()
		if !ok {
			logf("[PATH] no reachable app device, ending upgrade flow")
			return
		}

		logf("[PATH] app detected -> reboot to bootloader")
		err := sendUDPNoResponseOnPort(appBoard.IP, getUDPPort(), CM_Reboot)
		logf(err, "Failed to send reboot command to app device %s", appBoard.IP)

		wait := getRebootWaitDuration()
		logf("Waiting %.1f seconds for reboot...", wait.Seconds())
		time.Sleep(wait)
	}

	logf(true, "Failed to enter bootloader after multiple attempts.")
}

func ensureBootBoard() (boardInfo, bool) {
	if strings.TrimSpace(l_config.BootIP) == "" {
		logf("[PATH] bootIP empty -> discover bootloader")
		boards, err := discoverBoardsViaDirectedBroadcast("BOOTLD")
		if err != nil {
			logf("Bootloader discovery failed: %v", err)
			return boardInfo{}, false
		}
		selected := selectDiscoveredBoard(boards, l_config.UID)
		cacheBoardSelection(selected, true)
		return selected, true
	}

	logf("[PATH] bootIP cached -> ping %s", l_config.BootIP)
	info, err := udpPingAndValidateRole(l_config.BootIP, "BOOTLD")
	if err != nil {
		logf("Bootloader ping/status failed for bootIP=%s: %v", l_config.BootIP, err)
		return boardInfo{}, false
	}
	cacheBoardSelection(info, true)
	return info, true
}

func ensureAppBoard() (boardInfo, bool) {
	if strings.TrimSpace(l_config.AppIP) == "" {
		logf("[PATH] appIP empty -> discover app")
		boards, err := discoverBoardsViaDirectedBroadcast("CUSAPP")
		if err != nil {
			logf("CUSAPP discovery failed: %v", err)
			return boardInfo{}, false
		}
		selected := selectDiscoveredBoard(boards, l_config.UID)
		cacheBoardSelection(selected, false)
		return selected, true
	}

	logf("[PATH] appIP cached -> ping %s", l_config.AppIP)
	info, err := udpPingAndValidateRole(l_config.AppIP, "CUSAPP")
	if err != nil {
		logf("CUSAPP ping/status failed for appIP=%s: %v", l_config.AppIP, err)
		return boardInfo{}, false
	}
	cacheBoardSelection(info, false)
	return info, true
}

func cacheBoardSelection(selected boardInfo, isBoot bool) {
	if selected.UID != "" {
		l_config.UID = selected.UID
	}
	if isBoot {
		l_config.BootIP = selected.IP
	} else {
		l_config.AppIP = selected.IP
	}
	err := SaveConfig()
	logf(err, "Failed to save board cache")
	if isBoot {
		logf("Cached boot board: uid=%s bootIP=%s", l_config.UID, l_config.BootIP)
		return
	}
	logf("Cached app board: uid=%s appIP=%s", l_config.UID, l_config.AppIP)
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
		remoteAddr, rerr := net.ResolveUDPAddr("udp4", bcast+":"+getUDPPort())
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
		if !strings.EqualFold(info.Role, expectedRole) {
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

	return boardInfo{
		UID:     uid,
		IP:      ip,
		Role:    role,
		Version: strings.Join(parts[3:], "_"),
		Raw:     raw,
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

func selectDiscoveredBoard(boards []boardInfo, preferredUID string) boardInfo {
	if preferredUID != "" {
		for _, b := range boards {
			if b.UID == preferredUID {
				logf("Auto-selected device by cached UID=%s at ip=%s", b.UID, b.IP)
				return b
			}
		}
	}
	logf("Defaulting to the first discovered device: uid=%s ip=%s", boards[0].UID, boards[0].IP)
	return boards[0]
}

func udpPingAndGetStatus(ip string) (string, error) {
	buffer, _, err := sendUDPWithResponseOnPort(ip, getUDPPort(), CM_Ping, Timeout)
	if err != nil {
		return "", err
	}

	resp := strings.TrimSpace(string(buffer))
	if !strings.HasPrefix(resp, deviceReplyPrefix) {
		return "", fmt.Errorf("unexpected UDP ping response: %q", resp)
	}
	return resp, nil
}

func udpPingAndValidateRole(ip, expectedRole string) (boardInfo, error) {
	resp, err := udpPingAndGetStatus(ip)
	if err != nil {
		return boardInfo{}, err
	}

	info, ok := parseBoardInfoFromReply(resp, ip)
	if !ok {
		return boardInfo{}, fmt.Errorf("unexpected UDP ping response: %q", resp)
	}
	if !strings.EqualFold(info.Role, expectedRole) {
		return boardInfo{}, fmt.Errorf("unexpected device role %s from %s, expected %s", info.Role, ip, expectedRole)
	}
	return info, nil
}

func tryPing(serverAddr string) bool {
	buffer, _, err := sendUDPWithResponse(serverAddr, CM_Ping)
	if err != nil {
		logf("tryPing receive failed: %v", err)
		return false
	}

	resp := string(buffer)
	if resp == Rsp_Pong {
		logf("Ping success.")
		return true
	}

	logf("Ping failed, response: %s", resp)
	//deleteServerIPFile()
	return false
}

func discoverServer() string {
	broadcastAddr, err := getBroadcastAddress()
	if err != nil {
		logf("Broadcast address resolve failed: %v", err)
		return ""
	}

	for attempt := 1; attempt <= MaxRetries; attempt++ {
		logf("Broadcasting (%d)...", attempt)
		_, addr, err := sendUDPWithResponse(broadcastAddr, CM_PullIP)
		if err != nil {
			logf("Broadcast receive failed: %v", err)
			logf("Waiting for 2 seconds...")
			time.Sleep(2 * time.Second)
			continue
		}
		logf("Received from %s", addr.IP.String())
		return addr.IP.String()
	}

	return ""
}

// Send UDP message and wait for a response within Timeout duration.
// Returns response bytes and error (nil if success).
func sendUDPWithResponse(serverAddr, msg string) ([]byte, *net.UDPAddr, error) {
	return sendUDPWithResponseOnPort(serverAddr, s_udp_port, msg, Timeout)
}

func sendUDPWithResponseOnPort(serverAddr, port, msg string, timeout time.Duration) ([]byte, *net.UDPAddr, error) {
	// listen up from UDP port
	localAddr := &net.UDPAddr{IP: net.IPv4zero, Port: 0}
	conn, err := net.ListenUDP("udp4", localAddr)
	if err != nil {
		return nil, nil, fmt.Errorf("UDP listen failed: %w", err)
	}
	defer conn.Close()

	// set read timeout
	conn.SetDeadline(time.Now().Add(timeout))

	// prepare broadcast address
	remoteAddr, err := net.ResolveUDPAddr("udp4", serverAddr+":"+port)
	if err != nil {
		return nil, nil, fmt.Errorf("UDP ResolveUDPAddr failed: %w", err)
	}

	// broadcast send request
	_, err = conn.WriteToUDP([]byte(msg), remoteAddr)
	if err != nil {
		return nil, nil, fmt.Errorf("UDP WriteToUDP failed: %w", err)
	}
	logf("UDP sent: %s", msg)

	// receive reply
	buffer := make([]byte, Buf_s)
	n, addr, err := conn.ReadFromUDP(buffer)
	if err != nil {
		return nil, nil, fmt.Errorf("UDP ReadFromUDP Timeout: %w", err)
	}

	return buffer[:n], addr, nil
}

// Send UDP message without waiting for a response.
func sendUDPNoResponse(serverAddr, msg string) error {
	return sendUDPNoResponseOnPort(serverAddr, s_udp_port, msg)
}

func sendUDPNoResponseOnPort(serverAddr, port, msg string) error {
	conn, err := net.Dial("udp", serverAddr+":"+port)
	if err != nil {
		return fmt.Errorf("UDP connection failed: %w", err)
	}
	defer conn.Close()

	_, err = conn.Write([]byte(msg))
	if err != nil {
		return fmt.Errorf("UDP send failed: %w", err)
	}

	return nil
}

func getDirectedBroadcastAddrs() ([]string, error) {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil, err
	}

	seen := make(map[string]struct{})
	var addrs []string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
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
func RunEther_TCP(filePath, serverIP string) {
	logf("Trying to connect to TCP server at %s...", serverIP)

	conn, err := net.DialTimeout("tcp", serverIP+":"+getTCPPort(), Timeout)
	if err != nil {
		logf(true, "Failed to connect to server: %v", err)
	}
	defer conn.Close()

	if err := ping(conn); err != nil {
		logf(true, "Ping failed: %v", err)
	}

	if err := sendFile(conn, filePath); err != nil {
		logf(err, "File send failed: %v", err)
	}
}

func getUDPPort() string {
	if strings.TrimSpace(l_config.ServerPort) != "" {
		return strings.TrimSpace(l_config.ServerPort)
	}
	return defaultServerPort
}

func getTCPPort() string {
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
		if err := sendAndWaitOK(conn, []byte(CM_Ping)); err != nil {
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
func sendFile(conn net.Conn, filePath string) error {
	// Calculate checksum and file size
	checksum, fileSize, file := CalculateCRC32(filePath)
	defer file.(io.Closer).Close()
	logf("CRC Checksum: %x", checksum)

	// Send flash command

	flashCmd := fmt.Sprintf("%s %d %x", CM_Flash, fileSize, checksum)
	if err := sendAndWaitOK(conn, []byte(flashCmd)); err != nil {
		return fmt.Errorf("failed to send FLASH: %v", err)
	}
	//
	buf := make([]byte, Buf_b)
	for {
		n, readErr := file.Read(buf)
		if n > 0 {
			if err := sendAndWaitOK(conn, buf[:n]); err != nil {
				return fmt.Errorf("failed to send chunk: %v", err)
			}
		}
		if readErr == io.EOF {
			logf("File transfer complete.")
			break
		}
		if readErr != nil {
			return fmt.Errorf("file read error: %v", readErr)
		}
	}
	return nil
}

// 通用函数：发送数据，等待 server 回复 "ok"
func sendAndWaitOK(conn net.Conn, data []byte) error {
	conn.SetWriteDeadline(time.Now().Add(Timeout))
	dataLen, err := conn.Write(data)
	if err != nil {
		return fmt.Errorf("send error: %v", err)
	}

	logf("Sent %d bytes", dataLen)

	conn.SetReadDeadline(time.Now().Add(Timeout))
	ack := make([]byte, 4)
	n, err := conn.Read(ack)
	if err != nil {
		return fmt.Errorf("read ack error: %v", err)
	}
	if string(ack[:n]) != Rsp_OK {
		return fmt.Errorf("unexpected ack: %s", string(ack[:n]))
	}
	return nil
}
