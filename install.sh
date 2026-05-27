#!/bin/zsh
set -e

SCRIPT_DIR=${0:a:h}
cd "$SCRIPT_DIR"

echo "Building ClaudeUsage.app..."
venv/bin/python3 setup.py py2app 2>&1 | tail -5

if [[ ! -d dist/ClaudeUsage.app ]]; then
    echo "Build failed — dist/ClaudeUsage.app not found"
    exit 1
fi

echo "Stopping running instance (if any)..."
pkill -f "ClaudeUsage" 2>/dev/null || true

echo "Installing to /Applications..."
rm -rf /Applications/ClaudeUsage.app
cp -r dist/ClaudeUsage.app /Applications/ClaudeUsage.app

echo "Launching..."
open /Applications/ClaudeUsage.app

echo "Done."
