package testcase

import (
	"os"
	"regexp"
	"slices"
	"strings"
	"testing"

	"IAPTool/internal/ptproto"
)

// The fixture is the transcript case H4 captures by running the real firmware
// source natively (TestCase/host/porttool_caps). Testing against a hand-typed
// copy of what the board "should" say would only prove this file agrees with
// itself; testing against what the firmware actually printed is the point.
const goldenPath = "caps_golden.txt"

// transcript splits the fixture into the commands and the lines each produced.
func transcript(t *testing.T) map[string][][]string {
	t.Helper()
	raw, err := os.ReadFile(goldenPath)
	if err != nil {
		t.Fatalf("read fixture: %v\n"+
			"Run TestCase/host/porttool_caps/build.py to regenerate it.", err)
	}
	out := map[string][][]string{}
	for _, chunk := range strings.Split(string(raw), ">>> ")[1:] {
		lines := strings.Split(chunk, "\n")
		cmd := strings.TrimRight(lines[0], "\r")
		var body []string
		for _, l := range lines[1:] {
			l = strings.TrimRight(l, "\r")
			if l != "" && !strings.HasPrefix(l, "TEST ") {
				body = append(body, l)
			}
		}
		out[cmd] = append(out[cmd], body)
	}
	return out
}

func TestParseGoldenCaps(t *testing.T) {
	runs := transcript(t)["pt.caps"]
	// The first three are the ones the lifecycle test reads by position:
	// before any session, with din running, and after pt.stop all. Later runs
	// cover the per-channel parameters and may grow.
	if len(runs) < 3 {
		t.Fatalf("fixture has %d pt.caps runs, want at least 3", len(runs))
	}

	caps, err := ptproto.ParseCaps(runs[0])
	if err != nil {
		t.Fatalf("ParseCaps: %v", err)
	}

	if caps.Version != "0.9.0" {
		t.Errorf("version = %q, want 0.9.0", caps.Version)
	}
	// Deliberately not a hard port count: adding a port to the firmware is
	// meant to cost nothing here, and ParseCaps already refuses a reply whose
	// ports= disagrees with the rows that followed. What matters is that the
	// ports the panel is built around are all present and typed correctly.
	// rs485, rs232 and now can are sessions, not handover ports: their
	// hardware got a session, and one piece of hardware gets one row. Their
	// deep bring-up entries ride on that row as targets=.
	wantSessions := []string{"din", "dout", "relay", "ain", "aout", "temp",
		"rs232", "rs485", "can", "knx", "soak"}
	wantHandovers := []string{"bringup", "pwm"}
	// sd and sdram moved here when their checks became pt.run targets: the
	// chip's one-shot checks and its one-way soak entries are the same
	// hardware, so they share a row and it is the run row that anchors it.
	wantRuns := []string{"sd", "sdram", "rtc", "led"}

	for _, name := range wantSessions {
		p, ok := caps.Port(name)
		if !ok {
			t.Errorf("session %s missing", name)
			continue
		}
		if p.Kind != ptproto.KindSession {
			t.Errorf("%s kind = %s, want session", name, p.Kind)
		}
		if p.Values == nil {
			t.Errorf("%s has no current values", name)
		}
	}
	for _, name := range wantHandovers {
		p, ok := caps.Port(name)
		if !ok {
			t.Errorf("handover %s missing", name)
			continue
		}
		if p.Kind != ptproto.KindHandover || len(p.Targets) == 0 {
			t.Errorf("%s = %s with targets %v", name, p.Kind, p.Targets)
		}
	}
	for _, name := range wantRuns {
		p, ok := caps.Port(name)
		if !ok {
			t.Errorf("run port %s missing", name)
			continue
		}
		if p.Kind != ptproto.KindRun || len(p.Runs) == 0 {
			t.Errorf("%s = %s with runs %v", name, p.Kind, p.Runs)
		}
	}
	// The production plan checker looks a target up by name, so the run
	// targets a plan can name have to be reachable that way.
	sd, _ := caps.Port("sd")
	if !slices.Contains(sd.Runs, "sd.stress") {
		t.Errorf("sd runs = %v, want sd.stress among them", sd.Runs)
	}
	if !slices.Contains(sd.Targets, "sd.integrity.soak") {
		t.Errorf("sd targets = %v, want the one-way entry alongside", sd.Targets)
	}
}

func TestGoldenSessionDetail(t *testing.T) {
	caps, err := ptproto.ParseCaps(transcript(t)["pt.caps"][0])
	if err != nil {
		t.Fatal(err)
	}

	din, ok := caps.Port("din")
	if !ok {
		t.Fatal("no din port")
	}
	if din.Channels != 8 || din.Block != "D" || din.Loop != ptproto.LoopCtrl {
		t.Errorf("din = %d ch, blk %s, loop %s; want 8, D, ctrl",
			din.Channels, din.Block, din.Loop)
	}
	if din.Running {
		t.Error("din should not be running in the first caps")
	}
	if got := din.Values["period"]; got != "200" {
		t.Errorf("din period = %q, want 200", got)
	}

	// din's terminals are a plain run, so the labels are derived from term=.
	want := []string{"D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09"}
	if got := din.TerminalLabels(); !equal(got, want) {
		t.Errorf("din labels = %v, want %v", got, want)
	}

	relay, ok := caps.Port("relay")
	if !ok {
		t.Fatal("no relay port")
	}
	if relay.Channels != 6 {
		t.Errorf("relay channels = %d, want 6", relay.Channels)
	}
	// Six channels across twelve terminals: the firmware has to spell these
	// out because no rule derives them from B01-B12.
	wantRelay := []string{"B01+B02", "B03+B04", "B05+B06", "B07+B08", "B09+B10", "B11+B12"}
	if got := relay.TerminalLabels(); !equal(got, wantRelay) {
		t.Errorf("relay labels = %v, want %v", got, wantRelay)
	}
	if len(relay.Params) != 4 {
		t.Errorf("relay params = %v, want four of them", relay.Params)
	}
	for _, p := range relay.Params {
		if _, ok := relay.Values[p]; !ok {
			t.Errorf("relay advertises %s but reported no value", p)
		}
	}
}

func TestGoldenHandoverGrouping(t *testing.T) {
	caps, err := ptproto.ParseCaps(transcript(t)["pt.caps"][0])
	if err != nil {
		t.Fatal(err)
	}

	can, ok := caps.Port("can")
	if !ok {
		t.Fatal("no can port")
	}
	want := []string{"can", "can.soak", "can.scope", "can.echo"}
	if !equal(can.Targets, want) {
		t.Errorf("can targets = %v, want %v", can.Targets, want)
	}
	if can.Loop != ptproto.LoopLink {
		t.Errorf("can loop = %s, want link", can.Loop)
	}

	// Every target pt.handover lists must be reachable from exactly one port,
	// or the panel silently cannot start it.
	listed := map[string]bool{}
	for _, l := range transcript(t)["pt.handover"][0] {
		_, body := ptproto.Classify(l)
		if name, ok := strings.CutPrefix(body, "handover="); ok {
			listed[strings.Fields(name)[0]] = true
		}
	}
	if len(listed) != 14 {
		t.Fatalf("pt.handover listed %d targets, want 14", len(listed))
	}
	// A target is reachable from caps either as a handover port or on the
	// session row of the same hardware - rs485 has both a session and a deep
	// entry, and the panel shows them on one card.
	seen := map[string]int{}
	for _, p := range caps.Ports {
		for _, tg := range p.Targets {
			seen[tg]++
		}
	}
	// Deliberately absent: entering it takes the command loop away, so a
	// button for it would be a button that kills the panel. Named here so any
	// OTHER target going missing still fails.
	offPanel := map[string]bool{"rs232": true}

	for name := range listed {
		if offPanel[name] {
			if seen[name] != 0 {
				t.Errorf("%s is meant to be off the panel but caps offers it", name)
			}
			continue
		}
		if seen[name] != 1 {
			t.Errorf("target %s appears in %d caps groups, want exactly 1", name, seen[name])
		}
	}
	for name := range seen {
		if !listed[name] {
			t.Errorf("caps offers target %s that pt.handover does not list", name)
		}
	}
}

func TestGoldenLifecycle(t *testing.T) {
	runs := transcript(t)["pt.caps"]
	states := make([]ptproto.Port, 0, 3)
	for _, r := range runs {
		c, err := ptproto.ParseCaps(r)
		if err != nil {
			t.Fatal(err)
		}
		p, _ := c.Port("din")
		states = append(states, p)
	}
	if states[0].Running || !states[1].Running || states[2].Running {
		t.Errorf("din running across the three caps = %v/%v/%v, want false/true/false",
			states[0].Running, states[1].Running, states[2].Running)
	}
	if got := states[1].Values["ch"]; got != "1,3,5" {
		t.Errorf("running din ch = %q, want the 1,3,5 that pt.start asked for", got)
	}
}

func TestClassifyGoldenLines(t *testing.T) {
	// A bare log line is the protocol's fourth kind and is legal - it is the
	// bring-up printf a person reads. What must not happen is one appearing
	// where the PC is waiting for a reply and nobody knows what printed it, so
	// every source of prose is named here and anything else fails.
	//
	// pt.run is the first command in this transcript that produces any: the
	// checks it runs print as they go, before its OK line.
	// Matched by shape, not by a list of names: a new pt.run target should not
	// have to be registered here, while a stray printf still fails.
	prose := regexp.MustCompile(`^[A-Z][A-Z0-9_]*_TEST: `)
	for cmd, runs := range transcript(t) {
		for _, body := range runs {
			for _, l := range body {
				if k, _ := ptproto.Classify(l); k == ptproto.LineLog && !prose.MatchString(l) {
					t.Errorf("%s produced a line that reads as a bare log: %q", cmd, l)
				}
			}
		}
	}
	// A refusal must stay a refusal, with its reason intact.
	errLine := transcript(t)["pt.start din ch=1,9"][0][0]
	k, body := ptproto.Classify(errLine)
	if k != ptproto.LineErr || !strings.Contains(body, "must be channels") {
		t.Errorf("refusal did not survive classification: %q", errLine)
	}
}

func TestRejectedCapsShapes(t *testing.T) {
	good := transcript(t)["pt.caps"][0]

	cases := []struct {
		name  string
		lines []string
	}{
		{"empty", nil},
		{"header is not OK", []string{"ERR nope"}},
		{"lines= disagrees with what follows", good[:len(good)-1]},
		{"a port with an unknown kind", []string{
			"OK porttool=0.2.0 ports=1 lines=1",
			"OK port=x kind=mystery blk=- term=- channels=1 loop=none",
		}},
		{"a session that hides a parameter it advertises", []string{
			"OK porttool=0.2.0 ports=1 lines=1",
			"OK port=x kind=session blk=- term=- channels=1 loop=ctrl params=ch,period running=0 ch=1",
		}},
		{"an unknown loop", []string{
			"OK porttool=0.2.0 ports=1 lines=1",
			"OK port=x kind=session blk=- term=- channels=1 loop=sideways params= running=0",
		}},
		{"terms for a port that was never listed", []string{
			"OK porttool=0.2.0 ports=1 lines=2",
			"OK port=x kind=session blk=- term=- channels=1 loop=ctrl params= running=0",
			"OK terms=ghost A01,A02",
		}},
	}
	for _, tc := range cases {
		if _, err := ptproto.ParseCaps(tc.lines); err == nil {
			t.Errorf("%s: accepted, want an error", tc.name)
		}
	}
}

func TestGoldenFramesCarryTheEchoCounter(t *testing.T) {
	// The panel renders the link-health area from these three fields, and
	// renders the readings from the rest. Both have to survive parsing off the
	// same line, or one of the two areas silently goes blank.
	var frames []ptproto.Frame
	for _, runs := range transcript(t) {
		for _, body := range runs {
			for _, l := range body {
				if kind, b := ptproto.Classify(l); kind == ptproto.LineFrame {
					if f, ok := ptproto.ParseFrame(b); ok && f.Port == "din" {
						frames = append(frames, f)
					}
				}
			}
		}
	}
	if len(frames) == 0 {
		t.Fatal("the fixture has no din frames; run build.py to regenerate it")
	}
	for _, f := range frames {
		for _, k := range []string{"seq", "rx", "miss", "v"} {
			if _, ok := ptproto.Get(f.Fields, k); !ok {
				t.Fatalf("frame %q is missing %s=", f.Raw, k)
			}
		}
		if f.Tick == 0 {
			t.Errorf("frame %q has no board timestamp", f.Raw)
		}
	}
}

func TestParseFrame(t *testing.T) {
	f, ok := ptproto.ParseFrame("din t=48213 v=0x16 ch1=0 ch3=1")
	if !ok {
		t.Fatal("frame rejected")
	}
	if f.Port != "din" || f.Tick != 48213 {
		t.Errorf("port/tick = %s/%d, want din/48213", f.Port, f.Tick)
	}
	if v, _ := ptproto.Get(f.Fields, "v"); v != "0x16" {
		t.Errorf("v = %q, want 0x16", v)
	}
	if _, present := ptproto.Get(f.Fields, "t"); present {
		t.Error("t should have been consumed into Tick, not left in Fields")
	}
	if _, ok := ptproto.ParseFrame(""); ok {
		t.Error("an empty frame body should be rejected")
	}
}

func TestTickUnwrap(t *testing.T) {
	var u ptproto.TickUnwrapper
	if got := u.Unwrap(1000); got != 1000 {
		t.Errorf("first = %d, want 1000", got)
	}
	if got := u.Unwrap(2000); got != 2000 {
		t.Errorf("forward = %d, want 2000", got)
	}
	// 49.7 days in, the board's counter starts over. The timeline must not.
	if got := u.Unwrap(5); got != 1<<32+5 {
		t.Errorf("after wrap = %d, want %d", got, uint64(1)<<32+5)
	}
}

func TestFieldsKeepsDuplicates(t *testing.T) {
	// A repeated key means the firmware is emitting something ambiguous. The
	// parser has to preserve that so a test can catch it, rather than let a
	// map pick a winner.
	got := ptproto.Fields("a=1 b=2 a=3")
	if len(got) != 3 {
		t.Fatalf("got %d pairs, want 3 with the duplicate kept", len(got))
	}
	if v, _ := ptproto.Get(got, "a"); v != "1" {
		t.Errorf("Get returned %q, want the first occurrence", v)
	}
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
