// Test cases for the bootloader's TCP session rules: one client at a time, and
// an idle client does not hold the port forever.
package main

import (
	"fmt"
	"net"
	"os/exec"
	"time"
)

const (
	// The bootloader drops a connection that has been silent this long.
	idleKickAfter = 60 * time.Second
	// How far past the deadline T1 waits before calling it a miss.
	idleKickSlack = 15 * time.Second
	// How long T1b stays silent while expecting to survive.
	idleSurvive = 50 * time.Second

	dialTimeout = 5 * time.Second
)

func init() {
	register(testCase{id: "T1", title: "an idle connection is dropped after 60s", run: runT1})
	register(testCase{id: "T1b", title: "an idle connection survives 50s", run: runT1b})
	register(testCase{id: "T2", title: "a second connection is refused while one is open", run: runT2})
	register(testCase{id: "T4", title: "a new connection is accepted after the first closes", run: runT4})

	// Flashes the board, which then reboots into the application.
	register(testCase{id: "T3", title: "a second connection does not disturb a running transfer",
		destructive: true, run: runT3})
}

func dial(cfg config) (net.Conn, error) {
	return net.DialTimeout("tcp", net.JoinHostPort(cfg.ip, cfg.port), dialTimeout)
}

// alive reports whether the session still answers. "ping" is the cheapest
// command the bootloader implements and it replies "OK".
func alive(conn net.Conn) error {
	if err := conn.SetDeadline(time.Now().Add(dialTimeout)); err != nil {
		return err
	}
	if _, err := conn.Write([]byte("ping\n")); err != nil {
		return fmt.Errorf("write: %w", err)
	}
	buf := make([]byte, 64)
	n, err := conn.Read(buf)
	if err != nil {
		return fmt.Errorf("read: %w", err)
	}
	if n < 2 || string(buf[:2]) != "OK" {
		return fmt.Errorf("unexpected reply %q", string(buf[:n]))
	}
	return nil
}

// waitClosed blocks until the peer closes the connection or limit elapses.
// Returns how long it took, and whether a close was actually seen.
func waitClosed(conn net.Conn, limit time.Duration) (time.Duration, bool) {
	start := time.Now()
	if err := conn.SetReadDeadline(time.Now().Add(limit)); err != nil {
		return 0, false
	}
	buf := make([]byte, 64)
	for {
		_, err := conn.Read(buf)
		if err == nil {
			continue // unsolicited data: keep waiting for the close
		}
		if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
			return time.Since(start), false
		}
		return time.Since(start), true
	}
}

func runT1(cfg config) result {
	conn, err := dial(cfg)
	if err != nil {
		return fail("could not connect: %v", err)
	}
	defer conn.Close()

	fmt.Printf("    connected, staying silent for up to %s...\n", idleKickAfter+idleKickSlack)
	elapsed, closed := waitClosed(conn, idleKickAfter+idleKickSlack)
	if !closed {
		return fail("still connected after %s, expected a drop at ~%s", elapsed.Round(time.Second), idleKickAfter)
	}
	return pass("dropped after %s", elapsed.Round(time.Second))
}

func runT1b(cfg config) result {
	conn, err := dial(cfg)
	if err != nil {
		return fail("could not connect: %v", err)
	}
	defer conn.Close()

	fmt.Printf("    connected, staying silent for %s...\n", idleSurvive)
	elapsed, closed := waitClosed(conn, idleSurvive)
	if closed {
		return fail("dropped after only %s, must survive %s", elapsed.Round(time.Second), idleSurvive)
	}
	if err := alive(conn); err != nil {
		return fail("survived %s but stopped answering: %v", idleSurvive, err)
	}
	return pass("still connected and answering after %s", idleSurvive)
}

func runT2(cfg config) result {
	first, err := dial(cfg)
	if err != nil {
		return fail("could not open the first connection: %v", err)
	}
	defer first.Close()

	if err := alive(first); err != nil {
		return fail("first connection did not answer: %v", err)
	}

	second, err := dial(cfg)
	if err != nil {
		// Refused at connect time is the cleanest possible outcome.
		if err := alive(first); err != nil {
			return fail("second connection was refused but the first one broke: %v", err)
		}
		return pass("second connection refused (%v), first still answering", err)
	}
	defer second.Close()

	// Accepted at TCP level: it must then be dropped without being served.
	if err := alive(second); err == nil {
		return fail("second connection was accepted and served -- the board is talking to two clients")
	}
	if err := alive(first); err != nil {
		return fail("second connection was rejected but the first one broke: %v", err)
	}
	return pass("second connection accepted but not served, first still answering")
}

func runT3(cfg config) result {
	if cfg.binPath == "" {
		return fail("needs --bin=<file.bin>: this case runs a real upload")
	}

	cmd := exec.Command(cfg.iapTool, "ether", cfg.binPath, cfg.ip, "--downgrade=refuse")
	out, err := cmd.StdoutPipe()
	if err != nil {
		return fail("could not capture IAPTool output: %v", err)
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		return fail("could not start %s: %v", cfg.iapTool, err)
	}

	transferred := make(chan bool, 1)
	go watchForCompletion(out, transferred)

	// Let the transfer get going, then knock on the door.
	time.Sleep(8 * time.Second)
	second, dialErr := dial(cfg)
	intruderServed := false
	if dialErr == nil {
		intruderServed = alive(second) == nil
		second.Close()
	}

	waitErr := cmd.Wait()
	ok := <-transferred

	switch {
	case intruderServed:
		return fail("the intruding connection was served while a transfer was running")
	case waitErr != nil:
		return fail("the transfer failed while a second connection knocked: %v", waitErr)
	case !ok:
		return fail("IAPTool exited cleanly but never reported a completed transfer")
	}
	return pass("transfer completed; intruder was refused (dial err: %v)", dialErr)
}

func runT4(cfg config) result {
	first, err := dial(cfg)
	if err != nil {
		return fail("could not open the first connection: %v", err)
	}
	if err := alive(first); err != nil {
		first.Close()
		return fail("first connection did not answer: %v", err)
	}
	first.Close()

	// The board needs a moment to notice the FIN and free the slot.
	time.Sleep(2 * time.Second)

	second, err := dial(cfg)
	if err != nil {
		return fail("could not reconnect after a clean close: %v -- the slot is stuck", err)
	}
	defer second.Close()

	if err := alive(second); err != nil {
		return fail("reconnected but the session did not answer: %v -- the slot is stuck", err)
	}
	return pass("reconnected and served after the first session closed")
}
