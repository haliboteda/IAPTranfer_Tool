//go:build !windows && !linux

package serialx

import "go.bug.st/serial"

// Everywhere else - macOS in practice - the detailed enumeration needs cgo,
// and requiring cgo would cost the one-command three-platform cross-compile
// that compile_tool.sh depends on. Names only is the trade: a person on macOS
// picks by port name rather than by adapter description.
//
// Windows is the platform these tools are actually used on, so the loss is
// confined to a build nobody runs the panel from.
func listPorts() ([]PortInfo, error) {
	names, err := serial.GetPortsList()
	if err != nil {
		return nil, err
	}
	out := make([]PortInfo, 0, len(names))
	for _, n := range names {
		out = append(out, PortInfo{Name: n})
	}
	return out, nil
}
