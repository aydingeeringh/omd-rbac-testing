#!/usr/bin/env bash
###############################################################################
# Download the official docker-compose for a given OMD version.
#
# Usage:
#   ./scripts/get-compose.sh              # uses OMD_VERSION or defaults to 1.12.3
#   ./scripts/get-compose.sh 1.11.7       # specific version
#   OMD_VERSION=1.12.3 ./scripts/get-compose.sh
#
# Downloads from: github.com/open-metadata/OpenMetadata/releases
# Output: docker-compose-openmetadata.yml in the repo root
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VERSION="${1:-${OMD_VERSION:-1.12.3}}"
# Default to the full-stack postgres compose (includes Postgres, ES, OMD server, migration)
COMPOSE_NAME="${OMD_COMPOSE:-docker-compose-postgres.yml}"
URL="https://github.com/open-metadata/OpenMetadata/releases/download/${VERSION}-release/${COMPOSE_NAME}"
DEST="${REPO_DIR}/${COMPOSE_NAME}"

echo "Downloading official docker-compose for OMD ${VERSION} ..."
echo "  File: ${COMPOSE_NAME}"
echo "  URL:  ${URL}"
echo "  Dest: ${DEST}"
echo ""

if curl -fsSL -o "${DEST}" "${URL}"; then
  echo "Done. To start OMD ${VERSION}:"
  echo ""
  echo "  docker compose -f ${COMPOSE_NAME} up -d"
  echo ""
  echo "Note: Ensure Docker has at least 6 GB RAM and 4 vCPUs allocated."
  echo ""
  echo "Other compose variants (set OMD_COMPOSE to override):"
  echo "  docker-compose-postgres.yml   — Full stack with PostgreSQL (default)"
  echo "  docker-compose-mysql.yml      — Full stack with MySQL"
  echo "  docker-compose-openmetadata.yml — OMD server only (BYO database)"
else
  echo "ERROR: Could not download compose file for version ${VERSION}."
  echo "Check available versions at: https://github.com/open-metadata/OpenMetadata/releases"
  exit 1
fi
