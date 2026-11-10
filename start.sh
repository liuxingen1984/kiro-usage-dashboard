#!/usr/bin/env bash
# Start the dashboard at http://localhost:8501. It listens on 127.0.0.1 only,
# so other machines on your network cannot reach it.
# The first run creates app/venv and installs the Python dependencies.
#
#   ./start.sh                          run on port 8501
#   PORT=8502 ./start.sh                run on another port
#   ./start.sh --server.headless true   extra options are passed to Streamlit
set -euo pipefail
cd "$(dirname "$0")/app"

if [ ! -f .env ]; then
    echo "❌ app/.env not found. Run ./deploy.sh first (or ./deploy.sh env after a deploy)."
    exit 1
fi

find_python() {
    # $PYTHON if set, otherwise the first Python 3.10+ on the PATH.
    # (macOS's own /usr/bin/python3 is 3.9, so try versioned names too.)
    local candidate
    for candidate in ${PYTHON:-python3 python3.13 python3.12 python3.11 python3.10 python3.14}; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x venv/bin/streamlit ]; then
    if ! PYTHON=$(find_python); then
        echo "❌ Python 3.10 or newer is required and none was found on the PATH."
        echo "   Install one (e.g. 'brew install python@3.13'), or point to it:"
        echo "   PYTHON=/path/to/python3.13 ./start.sh"
        exit 1
    fi
    echo "📦 First run: creating app/venv with $PYTHON ($("$PYTHON" --version 2>&1)) and installing dependencies…"
    "$PYTHON" -m venv venv
    venv/bin/python -m pip install --quiet --upgrade pip
    venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec venv/bin/streamlit run app.py \
    --server.address 127.0.0.1 \
    --server.port "${PORT:-8501}" \
    --browser.gatherUsageStats false \
    "$@"
