#!/usr/bin/env bash
# Convenience wrapper — delegates to the Python module via uv.
# Usage: ./scripts/setup.sh [--config config/keytrade-glossary.json]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"
exec uv run omd-setup "$@"
