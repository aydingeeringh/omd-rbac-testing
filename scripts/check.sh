#!/usr/bin/env bash
# Convenience wrapper — runs preflight checks via uv.
# Usage: ./scripts/check.sh [--server http://localhost:8585/api/v1]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"
exec uv run omd-check "$@"
