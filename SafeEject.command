#!/bin/bash
# SafeEject launcher: double-click in Finder to open the dashboard.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
# Start from / so this terminal doesn't hold the disk SafeEject lives on.
cd / || exit 1

echo "======================================================="
echo "  ⏏️  Starting SafeEject..."
echo "======================================================="
echo "The dashboard will open in your browser."
echo ""

# The python3 on PATH can be an Intel build. Try Apple Silicon Homebrew first,
# then the macOS universal Python, and only use one that actually runs.
PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

# Otherwise check the python3 on PATH as well.
if [ -z "$PYTHON" ]; then
    candidate="$(command -v python3 2>/dev/null)"
    if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        PYTHON="$candidate"
    fi
fi

if [ -n "$PYTHON" ]; then
    # If SafeEject is already running, just open it instead of starting a second server.
    if /usr/bin/curl --silent --fail --max-time 2 http://127.0.0.1:5005/api/status >/dev/null 2>&1; then
        /usr/bin/open http://127.0.0.1:5005
        exit 0
    fi
    exec "$PYTHON" "$DIR/safe_eject.py" --port 5005
else
    echo "Error: Python 3.9 or newer wasn't found."
    echo "Run 'python3' once in Terminal to install the Command Line Tools, or install Python with Homebrew."
    read -p "Press Enter to close..."
fi
