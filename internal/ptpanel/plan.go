package ptpanel

// The plan page: load a plan file, edit its steps and limits, check it against
// the board that is connected, and run it.
//
// The point of doing this in the panel at all is who edits limits. A limit
// that only a person who can write JSON can change is a limit production and
// quality have to come and ask for - see DECISIONS.md 24.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"IAPTool/internal/ptplan"
	"IAPTool/internal/ptreport"
	"IAPTool/internal/ptseq"
)

// planState is everything the plan page needs that the manual panel does not.
type planState struct {
	mu      sync.Mutex
	running bool
	last    *ptreport.Report
	lastErr string
}

// PlanDir is where the panel looks for plan files. Set before Serve; empty
// means the plans directory beside the executable, then ./TestCase/plans.
var PlanDir string

func (s *Server) planRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/plans", s.handlePlanList)
	mux.HandleFunc("/api/plan", s.handlePlanLoadSave)
	mux.HandleFunc("/api/plan/check", s.handlePlanCheck)
	mux.HandleFunc("/api/plan/run", s.handlePlanRun)
}

func planDir() string {
	if PlanDir != "" {
		return PlanDir
	}
	if exe, err := os.Executable(); err == nil {
		beside := filepath.Join(filepath.Dir(exe), "plans")
		if st, err := os.Stat(beside); err == nil && st.IsDir() {
			return beside
		}
	}
	return filepath.Join("TestCase", "plans")
}

// safePlanPath keeps a request inside the plans directory. The panel binds to
// 127.0.0.1, but a path from a browser is still input, and ".." in it would
// read or overwrite whatever the operator's account can reach.
func safePlanPath(name string) (string, error) {
	if name == "" {
		return "", fmt.Errorf("no plan named")
	}
	if strings.ContainsAny(name, `/\`) || name == ".." {
		return "", fmt.Errorf("a plan name is a file in the plans folder, not a path")
	}
	if !strings.HasSuffix(name, ".json") {
		name += ".json"
	}
	return filepath.Join(planDir(), name), nil
}

func (s *Server) handlePlanList(w http.ResponseWriter, r *http.Request) {
	dir := planDir()
	entries, err := os.ReadDir(dir)
	out := map[string]any{"dir": dir}
	if err != nil {
		out["error"] = fmt.Sprintf("cannot read %s: %v", dir, err)
		writeJSON(w, http.StatusOK, out)
		return
	}
	var names []string
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	out["plans"] = names
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handlePlanLoadSave(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodPost {
		s.savePlan(w, r)
		return
	}
	path, err := safePlanPath(r.URL.Query().Get("name"))
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	plan, err := ptplan.Load(path)
	if err != nil {
		// The parser's own words: they name the step and the field, which is
		// more use than "could not load".
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"plan": plan, "name": filepath.Base(path)})
}

func (s *Server) savePlan(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string      `json:"name"`
		Plan ptplan.Plan `json:"plan"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	path, err := safePlanPath(body.Name)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	// Validated before it is written, never after: a plan file on disk that
	// does not load is one somebody will try to run on a line.
	if err := body.Plan.Validate(); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	data, err := json.MarshalIndent(body.Plan, "", "  ")
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	if err := os.WriteFile(path, append(data, '\n'), 0o644); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	s.emit("saved " + filepath.Base(path))
	writeJSON(w, http.StatusOK, map[string]any{"saved": filepath.Base(path)})
}

// handlePlanCheck reports what only the connected board can settle.
func (s *Server) handlePlanCheck(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Plan ptplan.Plan `json:"plan"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	if err := body.Plan.Validate(); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}

	s.mu.Lock()
	connected := s.board != nil
	caps := s.caps
	s.mu.Unlock()

	if !connected {
		writeJSON(w, http.StatusOK, map[string]any{
			"findings": []string{},
			"note":     "connect a board to check ports, parameters and limits",
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"findings": body.Plan.CheckAgainstCaps(caps)})
}

func (s *Server) handlePlanRun(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Plan ptplan.Plan `json:"plan"`
		SN   string      `json:"sn"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}
	if err := body.Plan.Validate(); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}

	s.mu.Lock()
	board := s.board
	caps := s.caps
	port := s.portNam
	echoWas := s.autoEcho
	if board != nil {
		// The executor answers the loop counters itself while a plan runs.
		// Leaving the panel's responder on as well would answer each frame
		// twice, and the board would count the second as a stale reply - a
		// miss on a link that is working.
		s.autoEcho = false
	}
	s.mu.Unlock()

	if board == nil {
		writeJSON(w, http.StatusOK, map[string]any{"error": "connect a board first"})
		return
	}

	s.plan.mu.Lock()
	if s.plan.running {
		s.plan.mu.Unlock()
		s.mu.Lock()
		s.autoEcho = echoWas
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]any{"error": "a plan is already running"})
		return
	}
	s.plan.running = true
	s.plan.mu.Unlock()

	defer func() {
		s.plan.mu.Lock()
		s.plan.running = false
		s.plan.mu.Unlock()
		s.mu.Lock()
		s.autoEcho = echoWas
		s.mu.Unlock()
	}()

	s.emit(fmt.Sprintf("running %s (limits %s)", body.Plan.Name, body.Plan.LimitVersion))

	runner := &ptseq.Runner{
		Board:       board,
		Caps:        &caps,
		Log:         func(line string) { s.emit(line) },
		ToolVersion: s.Version,
		SN:          body.SN,
		PortName:    port,
		BaseDir:     planDir(),
		// A UserConfirm step fails here rather than blocking: the run is one
		// synchronous request, so there is nowhere to put the question.
		Confirm: func(prompt string) (bool, error) {
			return false, fmt.Errorf(
				"this step needs an operator answer, which the panel cannot ask for yet: %s", prompt)
		},
	}

	report, err := runner.Run(body.Plan)
	if err != nil {
		s.plan.mu.Lock()
		s.plan.lastErr = err.Error()
		s.plan.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]any{"error": err.Error()})
		return
	}

	s.plan.mu.Lock()
	s.plan.last = &report
	s.plan.lastErr = ""
	s.plan.mu.Unlock()

	s.emit(report.Summary())
	writeJSON(w, http.StatusOK, map[string]any{"report": report, "summary": report.Summary()})
}

// planReportAge is only for the page's "last run" line.
func (s *Server) lastPlanReport() (*ptreport.Report, string, time.Time) {
	s.plan.mu.Lock()
	defer s.plan.mu.Unlock()
	if s.plan.last == nil {
		return nil, s.plan.lastErr, time.Time{}
	}
	return s.plan.last, s.plan.lastErr, s.plan.last.EndedAt
}
