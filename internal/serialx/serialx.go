// Package serialx is the one place either tool opens a serial port.
//
// IAPTool and PortTool both talk to the same boards through the same kinds of
// USB adapter, and the retry and timeout behaviour here was arrived at by
// watching real ones fail. Two copies of it would drift, and the drift would
// show up as "works in one tool, flaky in the other" on a bench, which is the
// most expensive place to find it.
package serialx

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"go.bug.st/serial"
)

// DefaultBaud is what every port on these boards runs at: the RS232 log and
// command line, the RS485 terminal, and the USB CDC channel alike.
const DefaultBaud = 115200

// Open opens a port at 8N1.
func Open(name string, baud int) (serial.Port, error) {
	port, err := serial.Open(name, &serial.Mode{BaudRate: baud})
	if err != nil {
		return nil, err
	}
	if port == nil {
		// Not observed, but a nil port with a nil error would surface much
		// later as a nil dereference in whoever writes to it first.
		return nil, errors.New("serial.Open returned nil port")
	}
	return port, nil
}

// RetryOpen keeps trying for `attempts`, waiting `gap` between tries, and
// reports each failure through `log` if one is given.
//
// The wait comes first on purpose: the usual reason a port will not open is
// that the board is still enumerating after a reset, so trying instantly only
// spends an attempt on a port the OS has not created yet.
func RetryOpen(name string, baud, attempts int, gap time.Duration, log func(string, ...any)) (serial.Port, error) {
	var lastErr error
	for i := 1; i <= attempts; i++ {
		time.Sleep(gap)
		port, err := Open(name, baud)
		if err == nil {
			if log != nil && i > 1 {
				log("opened %s on attempt %d", name, i)
			}
			return port, nil
		}
		lastErr = err
		if log != nil {
			log("could not open %s, attempt %d of %d: %v", name, i, attempts, err)
		}
	}
	return nil, fmt.Errorf("could not open %s after %d attempts: %w", name, attempts, lastErr)
}

// PortInfo is one serial port as the operating system describes it.
type PortInfo struct {
	Name    string // "COM7", "/dev/ttyUSB0"
	Product string // what the adapter calls itself, empty if it says nothing
	VID     string
	PID     string
	Serial  string
	IsUSB   bool
}

// Label is what to show a person choosing a port. Which physical adapter a
// COM number belongs to is exactly the thing nobody can tell from the number,
// and on a bench with four adapters plugged in that is the whole question.
func (p PortInfo) Label() string {
	var b strings.Builder
	b.WriteString(p.Name)
	if p.Product != "" {
		fmt.Fprintf(&b, " - %s", p.Product)
	}
	if p.VID != "" && p.PID != "" {
		fmt.Fprintf(&b, " [%s:%s]", p.VID, p.PID)
	}
	if p.Serial != "" {
		fmt.Fprintf(&b, " #%s", p.Serial)
	}
	return b.String()
}

// List reports every serial port the OS knows about, USB adapters described as
// fully as the platform allows - see enum_detailed.go and enum_basic.go.
//
// Ports with no USB details are still listed: a built-in port or one behind a
// driver that reports nothing is still a port somebody may need to pick.
func List() ([]PortInfo, error) {
	return listPorts()
}
