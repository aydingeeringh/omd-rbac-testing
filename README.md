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
#    Defaults to the full-stack postgres compose (includes Postgres, ES, OMD server, migration)
./scripts/get-compose.sh 1.12.3        # or any version: 1.11.7, 1.6.1, etc.

# 3. Start OMD (ensure Docker has >= 6 GB RAM, 4 vCPUs)
docker compose -f docker-compose-postgres.yml up -d
# Wait ~60 seconds for all services to initialise

# 4. Provision RBAC (locks down defaults, creates domains, policies, roles, teams, users, glossaries)
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
│   ├── client.py               # OMD API client (httpx-based, includes DB auth)
│   ├── setup.py                # RBAC provisioner (omd-setup CLI)
│   ├── test_runner.py          # Permission matrix tester (omd-test CLI)
│   └── preflight.py            # Environment checker (omd-check CLI)
├── config/
│   └── keytrade-glossary.json  # Example scenario config
├── scripts/                    # Optional shell wrappers
│   ├── get-compose.sh          # Downloads official OMD docker-compose
│   ├── setup.sh
│   ├── test-permissions.sh
│   └── check.sh
├── skill/
│   └── SKILL.md                # Claude skill for interactive RBAC testing
├── reports/                    # Auto-generated JSON test reports
├── pyproject.toml              # Python project config (uv / pip)
└── README.md
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `uv run omd-check` | Preflight check — verifies Python, Docker, uv, httpx, etc. |
| `uv run omd-setup -c CONFIG` | Provision domains, policies, roles, teams, users, glossaries |
| `uv run omd-test -c CONFIG` | Run permission matrix tests, generate pass/fail report |
| `uv run omd-test -c CONFIG -v` | Verbose — also shows which policy/rule produced each result |

## Key Features

### Default Policy Lockdown

OMD ships with built-in policies (`DataConsumerPolicy`, `OrganizationPolicy`) that grant edit permissions to all users by default. The framework automatically restricts these during setup via `default_policy_overrides` in the config:

```json
"default_policy_overrides": {
  "DataConsumerPolicy": {
    "action": "restrict",
    "remove_operations": ["EditDescription", "EditTags", "EditGlossaryTerms", "EditTier", "EditCertification"]
  },
  "OrganizationPolicy": {
    "action": "restrict",
    "remove_operations": ["EditOwners"]
  }
}
```

This ensures new users (SSO, admin-added) get strictly view-only access until explicitly assigned to a domain team with a role.

### Auto-Generated Team Structure

Instead of manually defining teams for each domain, set `"auto_teams": true` and the provisioner generates the standard structure automatically:

```json
"domains": [
  { "name": "engineering", "displayName": "Engineering", "domainType": "Aggregate" }
],
"auto_teams": true
```

This creates: `EngineeringTeam` (BusinessUnit), `EngineeringReaders`, `EngineeringWriters`, `EngineeringAdmins` (Groups with appropriate roles), plus a global `DefaultReaders` group. Adding a new domain is a one-liner.

### DRY Domain-Scoped Policies

Policies use `hasDomain()` / `!hasDomain()` conditions so they're defined once and automatically scope to whichever domain a user's team belongs to. Three policies + three roles cover unlimited domains.

### Sample Data Protection

Read-only roles include a `DenySampleData` deny rule to prevent readers from viewing raw data previews on tables, while still allowing metadata browsing.

## Authentication

Two modes, configurable via config JSON or environment variables:

### Basic Auth (default — local Docker / self-hosted)

```json
{
  "server": {
    "auth_type": "basic",
    "admin_email": "admin@open-metadata.org",
    "admin_password": "admin"
  }
}
```

Or via environment:
```bash
export OMD_AUTH_TYPE=basic
export OMD_ADMIN_EMAIL=admin@open-metadata.org
export OMD_ADMIN_PASSWORD=admin
```

### Token Auth (Collate cloud / JWT)

```json
{
  "server": {
    "auth_type": "token",
    "api_token": "your-jwt-or-api-token"
  }
}
```

Or via environment:
```bash
export OMD_AUTH_TYPE=token
export OMD_API_TOKEN=your-jwt-or-api-token
```

Environment variables always override the config file — keep credentials out of version control.

## Docker Compose

The compose structure changes between OMD versions. Always use the helper script to download the official compose file:

```bash
# Download the full-stack postgres compose (default)
./scripts/get-compose.sh 1.12.3

# Other variants available via OMD_COMPOSE env var:
OMD_COMPOSE=docker-compose-mysql.yml ./scripts/get-compose.sh 1.12.3       # MySQL backend
OMD_COMPOSE=docker-compose-openmetadata.yml ./scripts/get-compose.sh 1.12.3 # Server only (BYO database)

# Start
docker compose -f docker-compose-postgres.yml up -d
```

## Config File Format

The JSON config drives both setup and testing:

| Section | Purpose |
|---------|---------|
| `default_policy_overrides` | Lock down OMD's built-in permissive policies |
| `domains` | Domain definitions (name, displayName, type) |
| `auto_teams` | Set `true` to auto-generate team structure from domains |
| `auto_teams_roles` | Custom role name mapping (optional) |
| `policies` | Policy definitions with rules, operations, conditions |
| `roles` | Role definitions with policy assignments |
| `teams` | Explicit team hierarchy (used when `auto_teams` is false) |
| `extra_teams` | Additional teams merged with auto-generated ones |
| `users` | Test user accounts with team membership |
| `glossaries` | Glossary definitions with owner/reviewer teams, terms, domain |
| `test_matrix` | Test scenarios: user x resource x expected operations |

### Test Matrix Example

```json
{
  "name": "Steward creates/edits in own domain",
  "user": "steward@example.local",
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

The test harness treats `deny` and `notAllow` as equivalent (both mean "blocked").

## Creating Your Own Scenario

1. Copy `config/keytrade-glossary.json` to a new file
2. Edit domains, policies, roles, teams/auto_teams, users, glossaries
3. Define `test_matrix` with expected outcomes
4. Run:
   ```bash
   uv run omd-setup -c config/my-scenario.json
   uv run omd-test -c config/my-scenario.json
   ```

## Test Reports

Each run produces a JSON report in `reports/` with timestamp, pass/fail counts, pass rate, and per-assertion detail (test name, user, resource, operation, expected vs actual, policy & rule).

## Example Scenario

The included `keytrade-glossary.json` demonstrates a glossary governance pattern with three roles:

- **DataSteward** — creates and edits glossary terms within their domain
- **DataOwner** — views and reviews terms but cannot edit glossaries directly
- **DataConsumer** — view-only access to all data assets

Coverage includes same-domain CRUD, cross-domain isolation, and default policy lockdown verification.

## Claude Skill

The `skill/` directory contains a Claude skill for interactive RBAC testing from Claude Code or Cowork sessions. It includes lessons learned, debugging workflows, and common gotchas from testing across OMD versions. See `skill/SKILL.md`.
