#!/bin/sh
# Sets up BoringBots: installs uv if needed, then installs every bot into .venv.
# Safe to run again at any time.
set -eu

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (the tool that manages Python and the bots' dependencies)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make uv usable for the rest of this script; new terminal windows pick it up
    # automatically.
    PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing the bots..."
uv sync --all-packages

echo
echo "Done. To run a bot, from this folder:"
echo "  uv run dupebot <folder to scan>"
echo "  uv run formbot <form.pdf or form.docx>"
