---
name: omd-rbac-testing
description: "OpenMetadata RBAC testing and provisioning skill. Use whenever the user mentions RBAC testing, permission testing, OpenMetadata policies, OMD roles, glossary governance, domain-based access control, permission matrix, or wants to set up and verify role-based access in OpenMetadata/Collate. Also trigger when the user asks about OMD policy configuration, hasDomain() conditions, wants to test who can access what in their OMD instance, or wants to visually verify RBAC in the OMD UI using the Chrome extension."
---

# OpenMetadata RBAC Testing Skill

You are an expert in OpenMetadata (Collate) RBAC configuration and testing. You help users provision RBAC scenarios, run API-level permission matrix tests, AND perform UI-level verification using the Claude Chrome extension against a live OMD instance.

## Critical Lessons Learned (Read First!)

These are hard-won findings from testing across OMD 1.11.7 and 1.12.3. Failing to account for these will waste hours of debugging.

### 1. OMD Default Policies Are Dangerously Permissive

OMD ships with built-in policies that grant edit access to ALL users. You MUST lock these down before any RBAC testing or deployment:

**DataConsumerPolicy** — Grants `EditDescription`, `EditTags`, `EditGlossaryTerms`, `EditTier`, `EditCertification`, and `ViewAll` to every user. This completely undermines domain-scoped read-only roles.

**OrganizationPolicy** — Has a `noOwner()` rule that lets ANY user set themselves as owner of unowned resources. Also grants `All` operations to resource owners via `isOwner()`.

**Fix:** Use `default_policy_overrides` in the config to strip these before provisioning:
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

### 2. User Creation Requires Direct DB Manipulation

OMD's admin API (`PUT /users`) does NOT create login credentials. Users created this way cannot sign in. The `authenticationMechanism` field must be set directly in the database.

**Pattern:** Create user via API, then set bcrypt password hash + `isEmailVerified` in the `user_entity` table via `docker exec`:
```python
# The authenticationMechanism JSON structure (must match admin user's format):
{"config": {"password": "$2a$12$..."}, "authType": "BASIC"}
```

**What does NOT work:**
- `POST /users/signup` — requires email verification flow
- `POST /users/changePassword` — requires existing auth mechanism
- Base64 passwords — OMD uses bcrypt internally
- Bot JWT tokens — different auth path entirely

### 3. ViewSampleData Must Be Explicitly Denied

`ViewAll` is a meta-permission that includes `ViewSampleData`. If you want read-only users who can browse metadata but NOT see raw data previews, you must add an explicit deny rule:
```json
{
  "name": "DenySampleData",
  "effect": "deny",
  "operations": ["ViewSampleData"],
  "resources": ["All"]
}
```
Deny always takes precedence over allow in OMD.

### 4. OMD 1.12.x API Differences from 1.11.x

- **Teams PUT:** `parents` and `defaultRoles` expect UUID arrays, `domains` expects FQN string arrays
- **Team creation:** POST with `parents` field returns 400. Create teams first (no parent), then PUT to assign parent/role/domain in a second pass
- **Glossary domain:** PATCH with `/domain` or `/domains` fails with 500. Use PUT with `domains: ["fqn_string"]`
- **Glossary terms:** `glossary` field expects FQN string (e.g. `"MarketImpactGlossary"`), not an object `{id, type}`
- **Team PATCH 500:** Combined PATCH operations on teams cause 500 errors. Always use PUT instead

### 5. DRY Domain-Scoped Policies via hasDomain()

The `hasDomain()` / `!hasDomain()` conditions are the key to writing policies once and applying them across all domains. You do NOT need per-domain policies.

**Pattern:** 3 policies + 3 roles, defined once, work for unlimited domains:
- `DomainReadPolicy` — ViewAll + DenySampleData (no domain condition, applies everywhere)
- `DomainWritePolicy` — Deny writes outside domain (`!hasDomain()`), allow writes inside domain (`hasDomain()`)
- `DomainAdminPolicy` — Same as Write but includes Delete

### 6. auto_teams: Generate Team Structure from Domains

Instead of manually defining 4 teams per domain (BusinessUnit + Readers/Writers/Admins), set `"auto_teams": true` in the config. The provisioner auto-generates the standard structure from the domains list. Adding a new domain is then a one-liner — just add it to the `domains` array.

Custom role names can be overridden:
```json
"auto_teams_roles": {"reader": "DomainReader", "writer": "DomainWriter", "admin": "DomainAdmin"}
```

Extra teams (e.g. cross-domain groups) can be added via `"extra_teams": [...]`.

### 7. New User / SSO Guardrails

When someone signs in via SSO or is added by an admin, they land in the Organization team with no domain and no custom role. After locking down the default policies, they get:
- `ViewAll` / `ViewBasic` / `ViewCustomFields` — can browse all metadata
- Everything else is `notAllow` — can't edit, create, delete, or view sample data

Test this with a "default-reader" user in no domain team. This is your proof that the guardrails work.

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
- `src/omd_rbac/client.py` — Shared OMD API client (httpx-based, includes DB auth mechanism)
- `src/omd_rbac/setup.py` — RBAC provisioner (`uv run omd-setup`) with auto_teams + default policy lockdown
- `src/omd_rbac/test_runner.py` — Permission matrix tester (`uv run omd-test`)
- `src/omd_rbac/preflight.py` — Environment checker (`uv run omd-check`)
- `config/keytrade-glossary.json` — KeyTrade RBAC scenario (3 roles: Steward/Owner/Consumer)
- `config/spglobal-domains.json` — S&P Global scenario (3-tier: Reader/Writer/Admin per domain, auto_teams)
- `scripts/get-compose.sh` — Downloads official OMD docker-compose (defaults to postgres full-stack)
- `reports/` — Auto-generated JSON test reports

## Two-Layer Testing Strategy

This framework uses a **two-layer** approach to RBAC verification:

### Layer 1: API Permission Matrix (automated)
Tests every role x resource x operation combo against the OMD permissions API. Fast, repeatable, runs in CI. Answers: "Does the API grant/deny the expected access?"

### Layer 2: UI Verification via Chrome Extension (interactive)
Logs in as each test user in the actual OMD web UI and visually confirms that the interface correctly reflects the permissions. Answers: "Does the UI hide/disable/show the right controls for this role?"

Both layers are important — the API might report "deny" correctly, but a UI bug could still show an enabled button that produces an error on click.

## Workflow: API Testing

```bash
cd omd-rbac-testing
uv sync

# 1. Provision the RBAC scenario (locks down defaults, creates everything)
uv run omd-setup --config config/spglobal-domains.json

# 2. Run the permission matrix tests
uv run omd-test --config config/spglobal-domains.json

# 3. Verbose mode (shows which policy/rule produced each result)
uv run omd-test --config config/spglobal-domains.json --verbose
```

## Workflow: UI Verification via Chrome Extension

When the user wants to visually verify RBAC, use the Claude Chrome extension to drive the OMD web UI.

**Important Chrome extension limitation:** On localhost OMD, `read_page` and `navigate` work, but `computer` (clicks/screenshots) and `javascript_tool` may fail with "Cannot access a chrome-extension:// URL" errors. Workaround: the user logs in manually, then you use `read_page` to inspect the DOM for button/control presence.

### What to check per role:

**Reader:** Navigate to glossary pages. Verify NO "Add term" button, NO "Edit" button, NO action buttons on term rows, NO delete in 3-dots menu. Check both same-domain and cross-domain glossaries (should be identical — view-only everywhere).

**Writer:** Same-domain glossary should show "Add term", "Edit", action buttons on rows, "Rename" in 3-dots menu but NO "Delete". Cross-domain glossary should be view-only (no buttons at all).

**Admin:** Same-domain should show everything including "Delete" in 3-dots menu. Cross-domain should be view-only.

### Key UI elements to check via read_page:
- `"Add term"` text in a button — indicates create permission
- `"Edit"` text in a button — indicates edit permission
- `img "ellipsis"` — the 3-dots menu (user must click manually to reveal Delete/Rename options)
- Action buttons per table row — indicates edit/delete on individual terms
- `img "plus"` on Tags/Reviewers — indicates the user can add tags/reviewers

## Config Reference

### Minimal config with auto_teams:
```json
{
  "description": "My RBAC scenario",
  "omd_version": "1.12.3",
  "server": {
    "base_url": "http://localhost:8585/api/v1",
    "auth_type": "basic",
    "admin_email": "admin@open-metadata.org",
    "admin_password": "admin"
  },
  "default_policy_overrides": {
    "DataConsumerPolicy": {
      "action": "restrict",
      "remove_operations": ["EditDescription", "EditTags", "EditGlossaryTerms", "EditTier", "EditCertification"]
    },
    "OrganizationPolicy": {
      "action": "restrict",
      "remove_operations": ["EditOwners"]
    }
  },
  "domains": [
    { "name": "engineering", "displayName": "Engineering", "domainType": "Aggregate" },
    { "name": "analytics", "displayName": "Analytics", "domainType": "Aggregate" }
  ],
  "auto_teams": true,
  "policies": [
    // DomainReadPolicy, DomainWritePolicy, DomainAdminPolicy
  ],
  "roles": [
    // DomainReader, DomainWriter, DomainAdmin
  ],
  "users": [
    { "name": "eng-writer", "email": "eng-writer@test.local", "password": "Test@12345", "team": "EngineeringWriters" }
  ],
  "glossaries": [...],
  "test_matrix": [...]
}
```

### auto_teams naming convention:
For domain `{"name": "engineering", "displayName": "Engineering"}`:
- `EngineeringTeam` (BusinessUnit, domain=engineering)
- `EngineeringReaders` (Group, DomainReader, parent=EngineeringTeam)
- `EngineeringWriters` (Group, DomainWriter, parent=EngineeringTeam)
- `EngineeringAdmins` (Group, DomainAdmin, parent=EngineeringTeam)
- Plus a global `DefaultReaders` group (no domain)

The team names are derived by removing spaces from `displayName`. So "Market Impact" becomes "MarketImpact" prefix.

## Debugging Permissions

If a test fails or a user reports unexpected access:

**Step 1: Check which policy is granting/denying:**
```python
from omd_rbac.client import OMDClient
c = OMDClient('http://localhost:8585/api/v1', 'basic', 'admin@open-metadata.org', 'admin')
tok = c.get_user_token('user@test.local', 'Test@12345')
gid = c.resolve_resource_id('glossary', 'MyGlossary')
perms = c.get_permissions(tok, 'glossary', gid)
for op, detail in sorted(perms.items()):
    print(f'{op}: {detail["access"]} (policy={detail["policy"]}, rule={detail["rule"]})')
```

**Step 2: Check the built-in policies haven't reverted:**
OMD may reset built-in policies on upgrade. Always re-run `omd-setup` after upgrading.
```python
import json
p = c.get('/policies/name/DataConsumerPolicy?fields=rules')
print(json.dumps(p.get('rules', []), indent=2))
```

**Step 3: Check team membership and domain:**
```python
u = c.get('/users/name/my-user?fields=teams,domain')
print(f'Teams: {[t["name"] for t in u.get("teams", [])]}')
print(f'Domain: {u.get("domain", {}).get("name", "NONE")}')
```

## OMD RBAC Concepts (Quick Reference)

- **Policies** contain **rules**. Each rule has an effect (`allow`/`deny`), operations, resources, and an optional condition.
- **Deny rules always take precedence** over allow rules.
- **`hasDomain()`** returns true when the user's team domain matches the resource's domain.
- **`noOwner()`** returns true when a resource has no owner set.
- **`isOwner()`** returns true when the current user is the resource owner.
- **Roles** bundle policies. Users inherit all policies from their role.
- **Teams** can have a parent, a default role, and a domain. Users inherit accordingly.
- **Built-in policies** (DataConsumerPolicy, OrganizationPolicy) apply to ALL users regardless of role — you must explicitly restrict them.
- The OMD UI calls `/api/v1/permissions/{resourceType}/{id}` on page load to determine which controls to show/hide.

## Common Gotchas

1. **Tests pass but UI shows edit buttons** — Check if DataConsumerPolicy was locked down. It resets on OMD upgrade.
2. **User can't log in after creation** — `authenticationMechanism` not set in DB. Check `_set_basic_auth_in_db()` ran successfully.
3. **PATCH returns 500 on teams** — Use PUT instead. OMD 1.12.x has bugs with combined PATCH operations on teams.
4. **Glossary domain assignment fails** — Use PUT with `domains: ["fqn_string"]`, not PATCH.
5. **`deny` vs `notAllow`** — Both mean "blocked" but `deny` means an explicit deny rule matched, while `notAllow` means no rule granted access. The test runner treats them as equivalent.
6. **New domain added but no teams** — If using `auto_teams`, just add the domain to the `domains` array and re-run setup. Teams are generated automatically.
7. **Git lock files on macOS with Google Drive** — `rm -f .git/*.lock` before git operations.
