# OpenMetadata RBAC Testing Framework

Config-driven RBAC provisioning and permission testing for OpenMetadata (Collate). Works with any OMD version and supports both local Docker instances and Collate cloud.

Define your domains, policies, roles, teams, users, glossaries, and expected permissions in a single JSON file — then let the scripts set everything up and verify it automatically.

## Prerequisites

Run the built-in preflight check to verify your environment:

```bash
uv run omd-check
# Or with server connectivity test:
uv run omd-check --server http://localhost:8585/api/v1
```

**Required:**
- Python 3.9+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker & Docker Compose (for local OMD instances)

**Install uv** (if you don't have it):
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Quick Start (Local Docker)

```bash
cd omd-rbac-testing

# 1. Install dependencies
uv sync

# 2. Download the official docker-compose for your OMD version
#    (the compose structure changes between versions — always use this)
./scripts/get-compose.sh 1.12.3        # or any version: 1.11.7, 1.6.1, etc.

# 3. Start OMD (ensure Docker has >= 6 GB RAM, 4 vCPUs)
docker compose -f docker-compose-openmetadata.yml up -d
# Wait ~60 seconds for all services to initialise

# 4. Provision RBAC (domains, policies, roles, teams, users, glossaries)
uv run omd-setup --config config/keytrade-glossary.json

# 5. Run the permission test matrix
uv run omd-test --config config/keytrade-glossary.json

# 6. (Optional) Verbose mode — shows which policy/rule produced each result
uv run omd-test --config config/keytrade-glossary.json --verbose
```

Shell wrapper scripts are also provided for convenience:
```bash
./scripts/setup.sh --config config/keytrade-glossary.json
./scripts/test-permissions.sh --config config/keytrade-glossary.json
./scripts/check.sh --server http://localhost:8585/api/v1
```

## Quick Start (Collate Cloud)

```bash
cd omd-rbac-testing
uv sync

# Point at your Collate instance and use an API token
export OMD_BASE_URL=https://your-org.getcollate.io/api/v1
export OMD_AUTH_TYPE=token
export OMD_API_TOKEN=your-jwt-token-here

uv run omd-setup --config config/keytrade-glossary.json
uv run omd-test --config config/keytrade-glossary.json
```

## Cross-Platform Support

Everything runs on macOS, Linux, and Windows (via uv/Python). No platform-specific shell commands — all logic is in Python with `httpx` for HTTP instead of `curl`.

The shell wrappers in `scripts/` are optional convenience scripts (macOS + Linux). On Windows, use `uv run` directly.

## Repository Structure

```
omd-rbac-testing/
├── src/omd_rbac/               # Python package
│   ├── __init__.py
│   ├── client.py               # OMD API client (httpx-based)
│   ├── setup.py                # RBAC provisioner (omd-setup CLI)
│   ├── test_runner.py          # Permission matrix tester (omd-test CLI)
│   └── preflight.py            # Environment checker (omd-check CLI)
├── config/
│   └── keytrade-glossary.json  # Full scenario config
├── scripts/                    # Optional shell wrappers
│   ├── setup.sh
│   ├── test-permissions.sh
│   └── check.sh
├── skill/
│   └── SKILL.md                # Claude skill for interactive RBAC testing
├── reports/                    # Auto-generated JSON test reports
├── pyproject.toml              # Python project config (uv / pip)
├── docker-compose.yml          # OMD stack (version via OMD_VERSION env var)
├── .env.example                # Example environment config
└── README.md
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `uv run omd-check` | Preflight check — verifies Python, Docker, uv, httpx, etc. |
| `uv run omd-setup -c CONFIG` | Provision domains, policies, roles, teams, users, glossaries |
| `uv run omd-test -c CONFIG` | Run permission matrix tests, generate pass/fail report |
| `uv run omd-test -c CONFIG -v` | Verbose — also shows which policy/rule produced each result |

## Authentication

Two modes, configurable via config JSON or environment variables:

### Basic Auth (default — local Docker / self-hosted)

Config:
```json
{
  "server": {
    "auth_type": "basic",
    "admin_email": "admin@open-metadata.org",
    "admin_password": "admin"
  }
}
```

Or environment:
```bash
export OMD_AUTH_TYPE=basic
export OMD_ADMIN_EMAIL=admin@open-metadata.org
export OMD_ADMIN_PASSWORD=admin
```

### Token Auth (Collate cloud / JWT)

Config:
```json
{
  "server": {
    "auth_type": "token",
    "api_token": "your-jwt-or-api-token"
  }
}
```

Or environment:
```bash
export OMD_AUTH_TYPE=token
export OMD_API_TOKEN=your-jwt-or-api-token
```

Environment variables always override the config file — keep credentials out of version control.

## OMD Version

The docker-compose structure changes between OMD versions (different Elasticsearch versions, env vars, services). Always use the helper script to download the official compose file for your target version:

```bash
# Download the correct compose for any version
./scripts/get-compose.sh 1.12.3

# Start with that compose file
docker compose -f docker-compose-openmetadata.yml up -d
```

The repo also includes a `docker-compose.yml` with `${OMD_VERSION:-1.11.7}` image tags for backward compatibility, but `get-compose.sh` + the official file is the recommended approach.

The `omd_version` field in the config JSON is informational (labels which version the scenario was tested against).

## Config File Format

The JSON config drives both setup and testing:

| Section | Purpose |
|---------|---------|
| `omd_version` | Informational OMD version label |
| `server` | Base URL, auth type, credentials / token |
| `domains` | Domain definitions (name, type) |
| `policies` | Policy definitions with rules, operations, conditions |
| `roles` | Role definitions with policy assignments |
| `teams` | Team hierarchy, parent/child, role & domain assignments |
| `users` | Test user accounts with team membership |
| `glossaries` | Glossary definitions with owner/reviewer teams, terms, domain |
| `test_matrix` | Test scenarios: user x resource x expected operations |

### Test Matrix Example

```json
{
  "name": "Steward creates/edits in own domain",
  "user": "maxime@keytrade-test.local",
  "resource_type": "glossary",
  "resource": "MarketingGlossary",
  "expect": {
    "Create": "allow",
    "EditAll": "allow",
    "ViewAll": "allow",
    "Delete": "allow"
  }
}
```

The test harness treats `deny` and `notAllow` as equivalent.

## Creating Your Own Scenario

1. Copy `config/keytrade-glossary.json` to a new file
2. Edit domains, policies, roles, teams, users, glossaries
3. Define `test_matrix` with expected outcomes
4. Run:
   ```bash
   uv run omd-setup -c config/my-scenario.json
   uv run omd-test -c config/my-scenario.json
   ```

## Test Reports

Each run produces a JSON report in `reports/` with timestamp, pass/fail counts, pass rate, and per-assertion detail (test name, user, resource, operation, expected vs actual, policy & rule).

## KeyTrade Glossary Governance Scenario

The included `keytrade-glossary.json` tests a 3-policy model:

**Policies:** DataConsumerViewAllPolicy (view for everyone), DomainOnlyGovernancePolicy (deny writes outside domain), DataOwnerGlossaryDenyPolicy (deny glossary edits for Owners).

**Roles:** DataSteward (create/edit terms), DataOwner (view + approve only), DataConsumer (view-only).

**Coverage:** 8 scenarios, 31 assertions — steward CRUD, owner blocked, consumer view-only, cross-domain isolation.

## Claude Skill

The `skill/` directory contains a Claude skill for interactive RBAC testing from Claude Code or Cowork sessions. See `skill/SKILL.md`.
