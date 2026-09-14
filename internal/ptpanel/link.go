package ptpanel

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"strings"
	"sync"
	"time"

	"IAPTool/internal/serialx"
)

// A link port is the far end of a loop=link session: a second serial adapter,
// on the terminal being tested, whose whole job is to send back whatever the
// board sends it.
//
// This is what makes such a session mean anything. The board puts a number on
// the RS485 pair and takes the reply off that same pair, so the reply has to
// come back over the pair - answering on the control port instead would let the
// counter climb with the pair dead, and the firmware refuses pt.echo there for
// exactly that reason.
//
// It repeats the line verbatim rather than incrementing it: the board is what
// counts on, comparing the number that came back against the one it sent.
type linkPort struct {
	Board string // the board's port name, e.g. "rs485"
	COM   string // the adapter on this machine, or host:port for TCP
	Baud  int
	// Kind is "serial" or "tcp". The Ethernet session's far end is a
	// TCP client, not an adapter - a serial port has nothing to do with
	// it, and offering one was the panel telling people to plug the
	// wrong thing in (2026-09-14).
	Kind string

	port io.ReadWriteCloser
	stop func()

	mu      sync.Mutex
	rxLines uint64
	echoed  uint64
	lastErr string
}

func (l *linkPort) snapshot() map[string]any {
	l.mu.Lock()
	defer l.mu.Unlock()
	return map[string]any{
		"com":     l.COM,
		"kind":    l.Kind,
		"baud":    l.Baud,
		"rxLines": l.rxLines,
		"echoed":  l.echoed,
		"error":   l.lastErr,
	}
}

// bindLink opens the adapter and starts repeating whatever arrives on it.
func (s *Server) bindLink(boardPort, com string, baud int) (*linkPort, error) {
	if baud == 0 {
		baud = serialx.DefaultBaud
	}
	port, err := s.Open(com, baud)
	if err != nil {
		return nil, err
	}

	l := &linkPort{Board: boardPort, COM: com, Baud: baud, Kind: "serial", port: port}
	done := make(chan struct{})

	go func() {
		defer close(done)
		sc := bufio.NewScanner(l.port)
		sc.Buffer(make([]byte, 0, 512), 8*1024)
		for sc.Scan() {
			line := strings.TrimRight(sc.Text(), "\r")
			if line == "" {
				continue
			}

			l.mu.Lock()
			l.rxLines++
			l.mu.Unlock()
			s.linkLog(l, "收到 "+line)

			// Verbatim. The board compares what comes back against what it
			// sent, so changing it here would look exactly like a dead link.
			if _, err := io.WriteString(l.port, line+"\n"); err != nil {
				l.mu.Lock()
				l.lastErr = err.Error()
				l.mu.Unlock()
				s.linkLog(l, "回不出去："+err.Error())
				return
			}
			l.mu.Lock()
			l.echoed++
			l.lastErr = ""
			l.mu.Unlock()
			s.linkLog(l, "送回 "+line)
		}
		if err := sc.Err(); err != nil {
			l.mu.Lock()
			l.lastErr = err.Error()
			l.mu.Unlock()
			s.linkLog(l, "读不下去了："+err.Error())
		}
	}()

	l.stop = func() {
		_ = l.port.Close()
		<-done
	}
	return l, nil
}

// linkLog puts one line about a link port into the panel's log, so the bytes
// actually crossing the terminal are visible next to the board's own frames.
func (s *Server) linkLog(l *linkPort, what string) {
	s.emit(fmt.Sprintf("[link %s@%s] %s", l.Board, l.COM, what))
}

// emit pushes a line the panel itself produced to every open event stream.
//
// Kept separate from the board's own events: these are the panel's words, not
// the board's, and the log pane labels them so nobody reads a line the panel
// wrote as evidence from the hardware.
func (s *Server) emit(line string) {
	s.mu.Lock()
	s.emitSeq++
	ev := panelEvent{Seq: s.emitSeq, Line: line, At: time.Now()}
	if len(s.emitRing) >= emitRingCap {
		s.emitRing = s.emitRing[len(s.emitRing)-emitRingCap+1:]
	}
	s.emitRing = append(s.emitRing, ev)
	for _, c := range s.emitSubs {
		select {
		case c <- ev:
		default:
		}
	}
	s.mu.Unlock()
}

const emitRingCap = 4000

type panelEvent struct {
	Seq  uint64
	Line string
	At   time.Time
}

func (s *Server) subscribePanel(buffer int) (<-chan panelEvent, []panelEvent, func()) {
	ch := make(chan panelEvent, buffer)
	s.mu.Lock()
	if s.emitSubs == nil {
		s.emitSubs = map[int]chan panelEvent{}
	}
	id := s.emitNextSub
	s.emitNextSub++
	s.emitSubs[id] = ch
	backlog := make([]panelEvent, len(s.emitRing))
	copy(backlog, s.emitRing)
	s.mu.Unlock()

	return ch, backlog, func() {
		s.mu.Lock()
		if c, ok := s.emitSubs[id]; ok {
			delete(s.emitSubs, id)
			close(c)
		}
		s.mu.Unlock()
	}
}

// bindTCP connects to the board as a TCP client and echoes back whatever it
// sends.
//
// *** This is the far end the Ethernet session has always needed and the
// *** panel never had. *** The board runs the server; somebody has to connect
// to it, or conn= stays 0 and the card says "对端没连上" with no way to do
// anything about it from here. The command line had this all along, inside
// porttool run; the panel did not.
//
// Bytes, not lines. The board compares the counter that comes back against
// the one it sent, and a byte stream has no obligation to arrive in the same
// chunks it left in - splitting on newlines would work until a counter
// straddled two reads.
func (s *Server) bindTCP(boardPort, addr string) (*linkPort, error) {
	conn, err := net.DialTimeout("tcp", addr, 5*time.Second)
	if err != nil {
		return nil, err
	}

	l := &linkPort{Board: boardPort, COM: addr, Kind: "tcp", port: conn}
	done := make(chan struct{})

	go func() {
		defer close(done)
		buf := make([]byte, 4096)
		for {
			n, err := l.port.Read(buf)
			if n > 0 {
				l.mu.Lock()
				l.rxLines++
				l.mu.Unlock()
				if _, werr := l.port.Write(buf[:n]); werr != nil {
					l.mu.Lock()
					l.lastErr = werr.Error()
					l.mu.Unlock()
					s.linkLog(l, "回不出去："+werr.Error())
					return
				}
				l.mu.Lock()
				l.echoed++
				l.lastErr = ""
				l.mu.Unlock()
			}
			if err != nil {
				l.mu.Lock()
				l.lastErr = err.Error()
				l.mu.Unlock()
				return
			}
		}
	}()

	l.stop = func() {
		_ = l.port.Close()
		<-done
	}
	return l, nil
}
