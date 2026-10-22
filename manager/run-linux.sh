#!/bin/sh
# Start MK10 Manager. The first run sets up a private Python environment in .venv (needs internet).
set -e
cd "$(dirname "$0")"
if [ ! -f .venv/installed.ok ]; then
    echo "First run: setting things up. This needs internet and takes about a minute."
    [ -x .venv/bin/python ] || python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
    touch .venv/installed.ok
fi
exec .venv/bin/python launch.pyw "$@"
