package main

import (
	"bufio"
	"fmt"
	"io"
	"strings"
)

// watchForCompletion mirrors IAPTool's output and reports whether it announced a
// finished transfer. The line is IAPTool's own success marker, so this stays
// honest even if the process exits 0 for some other reason.
func watchForCompletion(r io.Reader, done chan<- bool) {
	found := false
	scanner := bufio.NewScanner(r)
	for scanner.Scan() {
		line := scanner.Text()
		fmt.Printf("    [IAPTool] %s\n", line)
		if strings.Contains(line, "File transfer complete") ||
			strings.Contains(line, "File transfer completed") {
			found = true
		}
	}
	done <- found
}
