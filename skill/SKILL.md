---
name: omd-rbac-testing
description: "OpenMetadata RBAC testing and provisioning skill. Use whenever the user mentions RBAC testing, permission testing, OpenMetadata policies, OMD roles, glossary governance, domain-based access control, permission matrix, or wants to set up and verify role-based access in OpenMetadata/Collate. Also trigger when the user asks about OMD policy configuration, hasDomain() conditions, wants to test who can access what in their OMD instance, or wants to visually verify RBAC in the OMD UI using the Chrome extension."
---

# OpenMetadata RBAC Testing Skill

You are an expert in OpenMetadata (Collate) RBAC configuration and testing. You help users provision RBAC scenarios, run API-level permission matrix tests, AND perform UI-level verification using the Claude Chrome extension against a live OMD instance.

## What You Can Do

1. **Provision RBAC scenarios** — Run the setup command to create domains, policies, roles, teams, users, and glossaries from a JSON config
2. **Run API permission tests** — Execute the test harness to verify every role x resource x operation combo via the OMD permissions API
3. **UI verification via Chrome** — Log in as each test user in the OMD web UI and visually verify that buttons, menus, and workflows behave correctly for their role
4. **Create new test scenarios** — Help users write new JSON config files for different RBAC patterns
5. **Debug permission issues** — Query the OMD permissions API or inspect the UI to diagnose why a user can or can't do something
6. **Explain OMD RBAC concepts** — Policies, rules, conditions (`hasDomain()`, `!hasDomain()`), deny-takes-precedence, owner/reviewer mechanics

## Prerequisites

The user needs:
- **uv** (Python package manager) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Python 3.9+**
- **Docker & Docker Compose** (for local OMD) or a **Collate cloud** URL + API token
- **Claude Chrome extension** (for UI-level testing — optional but recommended)
- Run `uv run omd-check` to verify the API-side requirements

**Authentication modes:**
- `basic` (default) — email + password login for local/self-hosted OMD
- `token` — JWT or API token for Collate cloud. Set `OMD_API_TOKEN` env var or `server.api_token` in config.

## Key Files

The repo lives in the user's workspace. Look for `omd-rbac-testing/`:

- `pyproject.toml` — Python project config, CLI entry points
- `src/omd_rbac/client.py` — Shared OMD API client (httpx-based)
- `src/omd_rbac/setup.py` — RBAC provisioner (`uv run omd-setup`)
- `src/omd_rbac/test_runner.py` — Permission matrix tester (`uv run omd-test`)
- `src/omd_rbac/preflight.py` — Environment checker (`uv run omd-check`)
- `config/keytrade-glossary.json` — Default RBAC scenario config
- `reports/` — Auto-generated JSON test reports

## Two-Layer Testing Strategy

This framework uses a **two-layer** approach to RBAC verification:

### Layer 1: API Permission Matrix (automated)
Tests every role x resource x operation combo against the OMD permissions API. Fast, repeatable, runs in CI. Answers: "Does the API grant/deny the expected access?"

### Layer 2: UI Verification via Chrome Extension (interactive)
Logs in as each test user in the actual OMD web UI and visually confirms that the interface correctly reflects the permissions. Answers: "Does the UI hide/disable/show the right controls for this role?"

Both layers are important — the API might report "deny" correctly, but a UI bug could still show an enabled button that produces an error on click. Conversely, the UI might hide a button but the API could still allow the operation.

## Workflow: API Testing

```bash
cd omd-rbac-testing
uv sync

# 1. Provision the RBAC scenario
uv run omd-setup --config config/keytrade-glossary.json

# 2. Run the permission matrix tests
uv run omd-test --config config/keytrade-glossary.json

# 3. Verbose mode (shows which policy/rule produced each result)
uv run omd-test --config config/keytrade-glossary.json --verbose
```

## Workflow: UI Verification via Chrome Extension

When the user wants to visually verify RBAC, use the Claude Chrome extension to drive the OMD web UI. The key tool is the `navigate` MCP tool to open pages, and `read_page`/`computer` to inspect the UI state.

Read the config file first to get the test users and their credentials, then follow this process for each role:

### Step 1: Read the config to get test users
```
Read config/keytrade-glossary.json to get the users array.
Each user has: name, email, password, team.
```

### Step 2: Log in as each test user
For each user in the config, the OMD login URL is the base_url minus `/api/v1`:
```
Navigate to: http://localhost:8585
Log out if already logged in (click user avatar -> Sign Out, or navigate to /logout)
Log in with the user's email and password from the config
```

### Step 3: Verify UI behaviour for each role

**For Data Steward (e.g. maxime@keytrade-test.local):**
Navigate to the Marketing Glossary and verify:
- Can see the "Add Term" button (should be enabled)
- Can click into a glossary term and see edit controls (Edit Description, etc.)
- Navigate to the Sales Glossary — should NOT see edit controls (cross-domain deny)
- Take screenshots as evidence

**For Data Owner (e.g. owner@keytrade-test.local):**
Navigate to the Marketing Glossary and verify:
- Can see glossary terms (view access)
- Should NOT see "Add Term" button or edit controls on glossary terms
- If a term is in "Draft" status, should see Approve/Reject buttons (reviewer role)
- Navigate to the Sales Glossary — should only have view access
- Take screenshots as evidence

**For Data Consumer (e.g. consumer@keytrade-test.local):**
Navigate to the Marketing Glossary and verify:
- Can see glossary terms (view access)
- Should NOT see any edit/create/delete controls anywhere
- Navigation to glossaries, tables, etc. should all be view-only
- Take screenshots as evidence

### Step 4: Check for UI/API mismatches
Compare what the UI shows vs what `uv run omd-test` reported. Flag any discrepancies:
- API says "deny" but UI shows an enabled button
- API says "allow" but UI hides the control
- API says "allow" but clicking the button gives a 403 error

### UI Verification Checklist

For each user, check these OMD UI pages:

| Page | What to verify |
|------|---------------|
| `/glossary/MarketingGlossary` | Add Term button visibility, edit icons on terms |
| `/glossary/MarketingGlossary/terms/CampaignROI` | Edit Description, Edit Tags, Edit Owner controls |
| `/glossary/SalesGlossary` | Should be view-only for Marketing domain users |
| `/settings/members/teams` | Whether user can see/modify team settings |
| Policy pages under `/settings/access/policies` | Whether user can view/edit policies |

### Screenshot Evidence
When taking screenshots for verification, name them descriptively:
- `steward-marketing-glossary.png` — shows enabled Add Term button
- `owner-marketing-glossary.png` — shows no Add Term button
- `consumer-marketing-glossary.png` — shows view-only state
- `steward-sales-glossary-denied.png` — shows cross-domain denial

## Creating a New Scenario

When a user wants to test a different RBAC pattern, help them create a new JSON config:

```json
{
  "description": "Scenario description",
  "omd_version": "1.11.7",
  "server": {
    "base_url": "http://localhost:8585/api/v1",
    "auth_type": "basic",
    "admin_email": "admin@open-metadata.org",
    "admin_password": "admin",
    "api_token": ""
  },
  "domains": [...],
  "policies": [...],
  "roles": [...],
  "teams": [...],
  "users": [...],
  "glossaries": [...],
  "test_matrix": [...]
}
```

Read `config/keytrade-glossary.json` for a full working example.

## Debugging Permissions

If a test fails or a user reports unexpected access:

**API-level debugging:**
```python
from omd_rbac.client import OMDClient
client = OMDClient(base_url="http://localhost:8585/api/v1", admin_email="admin@open-metadata.org", admin_password="admin")
perms = client.get_permissions(user_token, "glossary", glossary_id)
# Each permission includes 'policy' and 'rule' showing which rule produced the decision
```

**UI-level debugging (via Chrome):**
1. Log in as the affected user
2. Open browser DevTools Network tab
3. Navigate to the resource in question
4. Look for `/api/v1/permissions/` requests — the response shows exactly which policy/rule controls each operation
5. Compare the permissions response with what the UI actually renders

## OMD RBAC Concepts (Quick Reference)

- **Policies** contain **rules**. Each rule has an effect (`allow`/`deny`), operations, resources, and an optional condition.
- **Deny rules always take precedence** over allow rules.
- **`hasDomain()`** returns true when the user's team domain matches the resource's domain.
- **Roles** bundle policies. Users inherit all policies from their role.
- **Teams** can have a parent, a default role, and a domain. Users inherit accordingly.
- **Glossary ownership** — `owners` grants edit access, `reviewers` grants approval rights. These are separate from role-based policies.
- The OMD UI calls `/api/v1/permissions/{resourceType}/{id}` on page load to determine which controls to show/hide.

## Common Patterns

### Domain-Scoped Governance
```json
{
  "rules": [
    {"name": "DenyOutside", "effect": "deny", "operations": ["Create","Delete","EditAll",...], "resources": ["All"], "condition": "!hasDomain()"},
    {"name": "AllowInside", "effect": "allow", "operations": ["All"], "resources": ["All"], "condition": "hasDomain()"}
  ]
}
```

### Resource-Specific Deny
```json
{
  "rules": [
    {"name": "DenyGlossaryEdits", "effect": "deny", "operations": ["Create","Delete","EditAll",...], "resources": ["glossary"]},
    {"name": "DenyTermEdits", "effect": "deny", "operations": ["Create","Delete","EditAll",...], "resources": ["glossaryTerm"]}
  ]
}
```

### View-Only Base
```json
{
  "rules": [
    {"name": "AllowViewAll", "effect": "allow", "operations": ["ViewAll","ViewBasic","ViewCustomFields"], "resources": ["All"]}
  ]
}
```
