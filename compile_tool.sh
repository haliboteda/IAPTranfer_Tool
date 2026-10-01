#!/bin/bash

# Define output directory
OUTPUT_DIR="./Output"

# Platforms to build: "GOOS:extension"
PLATFORMS=(
    "windows:.exe"
    "darwin:"
    "linux:"
)

for entry in "${PLATFORMS[@]}"; do
    PLATFORM="${entry%%:*}"
    EXTENSION="${entry#*:}"

    PLATFORM_DIR="$OUTPUT_DIR/$PLATFORM"
    OUTPUT_FILE="$PLATFORM_DIR/IAPTool$EXTENSION"

    mkdir -p "$PLATFORM_DIR"

    echo "Building IAPTool for the '$PLATFORM' platform..."
    GOOS=$PLATFORM GOARCH=amd64 go build -o "$OUTPUT_FILE" .

    if [ $? -eq 0 ]; then
        echo "Build succeeded! The executable is saved at: $OUTPUT_FILE"
    else
        echo "Build failed for IAPTool on '$PLATFORM'. Please check the error messages."
        exit 1
    fi
done

# The Arduino IDE's Upload button runs the copy inside the board package, not
# the one in Output/. Every build lands there too, so the menu can never drive
# an older binary -- P11 (TestCase/tools/check_tool_sync.py) is what catches it
# when this step is skipped. Machine paths stay in TestCase/config/machine.py,
# which is why the copying is done by the Python helper rather than here.
INSTALLER="./TestCase/tools/install_tool.py"
if [ -f "$INSTALLER" ]; then
    # Try each candidate by actually running it: on Windows "python3" on PATH is
    # often the Store stub, which exists, is executable, and refuses to run.
    PY=""
    for CAND in python3 python py; do
        if "$CAND" -c "pass" >/dev/null 2>&1; then PY="$CAND"; break; fi
    done
    if [ -n "$PY" ]; then
        echo
        "$PY" "$INSTALLER" || echo "Could not install into the board package - the IDE keeps using the previous build."
    else
        echo "No python on PATH: skipping the copy into the board package."
    fi
fi
