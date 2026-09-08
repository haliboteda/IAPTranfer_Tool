package main

// The production side of the one binary: `porttool validate` reads a plan
// without a board, `porttool run` executes one against a board and writes the
// report. The panel and these two share ptcheck, so a limit cannot mean one
// thing on screen and another on the line - see DECISIONS.md 8 and 24.

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"IAPTool/internal/ptboard"
	"IAPTool/internal/ptplan"
	"IAPTool/internal/ptreport"
	"IAPTool/internal/ptseq"
	"IAPTool/internal/serialx"
	"IAPTool/internal/simboard"
)

// cmdValidate reads a plan and says what is wrong with it, without a board.
func cmdValidate(args []string) int {
	fs := flag.NewFlagSet("validate", flag.ContinueOnError)
	fs.Usage = func() {
		fmt.Fprint(os.Stderr, "porttool validate <plan.json>\n")
	}
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() != 1 {
		fs.Usage()
		return 2
	}

	plan, err := ptplan.Load(fs.Arg(0))
	if err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		return 1
	}
	enabled := 0
	for _, s := range plan.Steps {
		if s.IsEnabled() {
			enabled++
		}
	}
	fmt.Printf("%s (limits %s): %d step(s), %d enabled\n",
		plan.Name, plan.LimitVersion, len(plan.Steps), enabled)
	fmt.Println("The plan is well formed. Ports and parameters are only checked against a board, by `porttool run`.")
	return 0
}

// cmdRun executes a plan against a board.
func cmdRun(args []string) int {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	port := fs.String("port", "", "serial port of the board, e.g. COM7")
	baud := fs.Int("baud", serialx.DefaultBaud, "baud rate of the control port")
	sn := fs.String("sn", "", "serial number to record in the report")
	jsonOut := fs.String("json", "", "write the full report here")
	csvOut := fs.String("csv", "", "write one row per check here")
	yes := fs.Bool("yes", false, "answer every operator prompt with pass")
	fs.Usage = func() {
		fmt.Fprint(os.Stderr, `porttool run <plan.json> --port COM7 [options]

  --port COM7      the board's RS232 control port (required)
  --baud N         default 115200
  --sn TEXT        serial number to record, and what a ReadSN step with
                   source "arg" reads
  --json FILE      write the full report, raw values included
  --csv FILE       write one row per check
  --yes            answer operator prompts with pass, for an unattended run

Exit code: 0 every step passed, 1 something failed, 2 could not run.
`)
	}
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() != 1 || *port == "" {
		fs.Usage()
		return 2
	}

	plan, err := ptplan.Load(fs.Arg(0))
	if err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		return 2
	}

	var rw io.ReadWriteCloser
	if simboard.IsSim(*port) {
		// A plan run against the simulated board. Useful for checking that a
		// plan is well-formed and that the report comes out right; it says
		// nothing about hardware, and the report records the port name so
		// nobody has to wonder afterwards which kind of run they are reading.
		rw, err = simboard.Open()
	} else {
		rw, err = serialx.Open(*port, *baud)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "Could not open %s: %v\n", *port, err)
		return 2
	}
	board := ptboard.New(rw, 0)
	defer board.Close()

	caps, err := board.Caps()
	if err != nil {
		fmt.Fprintf(os.Stderr, `The board on %s did not answer pt.caps: %v

Check the RS232 wiring (C05 TxD / C06 RxD / C02 GND) and that the image was
built with PORTTOOL_ENABLE=1.
`, *port, err)
		return 2
	}

	// Findings that need the board: a port the firmware does not have, a
	// parameter it will not accept, or a limit on a counter that does not
	// travel over the port being judged.
	if findings := plan.CheckAgainstCaps(caps); len(findings) > 0 {
		fmt.Fprintf(os.Stderr, "This plan does not fit the firmware on %s (porttool %s):\n", *port, caps.Version)
		for _, f := range findings {
			fmt.Fprintf(os.Stderr, "  - %s\n", f)
		}
		return 2
	}

	runner := &ptseq.Runner{
		Board:       board,
		Caps:        &caps,
		Log:         func(s string) { fmt.Println(s) },
		ToolVersion: version,
		SN:          *sn,
		PortName:    *port,
		Confirm:     confirmFunc(*yes),
		// A path in a plan is relative to the plan, so a plan and the files it
		// names can be copied to a production PC together.
		BaseDir: filepath.Dir(fs.Arg(0)),
	}

	fmt.Printf("%s (limits %s) against porttool %s on %s\n\n",
		plan.Name, plan.LimitVersion, caps.Version, *port)

	report, err := runner.Run(plan)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		return 2
	}

	fmt.Printf("\n%s\n", report.Summary())

	if code := writeReports(report, *jsonOut, *csvOut); code != 0 {
		return code
	}
	if !report.Passed() {
		return 1
	}
	return 0
}

func writeReports(report ptreport.Report, jsonPath, csvPath string) int {
	for _, out := range []struct {
		path  string
		write func(io.Writer) error
	}{
		{jsonPath, report.WriteJSON},
		{csvPath, report.WriteCSV},
	} {
		if out.path == "" {
			continue
		}
		f, err := os.Create(out.path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Could not write %s: %v\n", out.path, err)
			return 2
		}
		err = out.write(f)
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
		if err != nil {
			fmt.Fprintf(os.Stderr, "Could not write %s: %v\n", out.path, err)
			return 2
		}
		fmt.Printf("wrote %s\n", out.path)
	}
	return 0
}

// confirmFunc asks on the terminal, or answers for the operator when the run
// is unattended.
//
// --yes has to be asked for: a step that exists because only a person can see
// the answer, quietly passing itself, is worse than having no step at all.
func confirmFunc(auto bool) func(string) (bool, error) {
	if auto {
		return func(prompt string) (bool, error) {
			fmt.Printf("  %s -> pass (--yes)\n", prompt)
			return true, nil
		}
	}
	in := bufio.NewReader(os.Stdin)
	return func(prompt string) (bool, error) {
		fmt.Printf("\n  %s\n  pass? [y/N] ", prompt)
		line, err := in.ReadString('\n')
		if err != nil && line == "" {
			return false, fmt.Errorf("could not read an answer: %w", err)
		}
		answer := strings.ToLower(strings.TrimSpace(line))
		return answer == "y" || answer == "yes", nil
	}
}
