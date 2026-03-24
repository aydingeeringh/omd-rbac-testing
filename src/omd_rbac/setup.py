"""Config-driven RBAC provisioner for OpenMetadata.

Reads a JSON config and provisions domains, policies, roles, teams,
users, glossaries, and glossary terms via the OMD REST API.

Usage (via uv):
    uv run omd-setup --config config/keytrade-glossary.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from omd_rbac.client import OMDClient

# ── ANSI colours ────────────────────────────────────────────────────────
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"


def log(msg: str) -> None:
    print(f"{GREEN}[✓]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[!]{NC} {msg}")


def err(msg: str) -> None:
    print(f"{RED}[✗]{NC} {msg}")


def header(msg: str) -> None:
    print(f"\n{BOLD}{BLUE}── {msg} ──{NC}")


def camel_to_display(name: str) -> str:
    """DataSteward -> Data Steward."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", name)


# ── Idempotent helpers ──────────────────────────────────────────────────

def create_or_update_policy(client: OMDClient, policy: dict) -> None:
    name = policy["name"]
    resp = client.post("/policies", policy)
    if resp.status_code in (200, 201):
        log(f"{name} created")
    elif resp.status_code == 409:
        warn(f"{name} exists — updating rules ...")
        pid = client.extract_id(client.get(f"/policies/name/{name}"))
        if pid:
            client.patch(f"/policies/{pid}", [
                {"op": "replace", "path": "/rules", "value": policy["rules"]}
            ])
            log(f"{name} rules updated")
    else:
        err(f"{name} creation failed (HTTP {resp.status_code})")


def create_or_update_role(client: OMDClient, name: str, policy_csv: str, desc: str) -> None:
    policy_names = [p.strip() for p in policy_csv.split(",")]
    body = {
        "name": name,
        "displayName": camel_to_display(name),
        "description": desc,
        "policies": policy_names,
    }
    resp = client.post("/roles", body)
    if resp.status_code in (200, 201):
        log(f"{name} role created [{policy_csv}]")
    elif resp.status_code == 409:
        warn(f"{name} role exists — updating policies ...")
        rid = client.extract_id(client.get(f"/roles/name/{name}"))
        refs = []
        for pname in policy_names:
            pid = client.extract_id(client.get(f"/policies/name/{pname}"))
            if pid:
                refs.append({"id": pid, "type": "policy"})
        if rid:
            client.patch(f"/roles/{rid}", [
                {"op": "replace", "path": "/policies", "value": refs}
            ])
            log(f"{name} role policies updated")
    else:
        err(f"{name} role creation failed (HTTP {resp.status_code})")


# ── Provisioning steps ──────────────────────────────────────────────────

def restrict_default_policies(client: OMDClient, cfg: dict) -> None:
    """Restrict or disable OMD's built-in default policies.

    OMD ships with a DataConsumerPolicy that grants EditDescription to ALL
    users by default.  This undermines domain-scoped RBAC because even users
    with no explicit write role can edit descriptions on any resource.

    Config format:
        "default_policy_overrides": {
            "DataConsumerPolicy": {
                "action": "restrict",
                "remove_operations": ["EditDescription"]
            }
        }

    Supported actions:
        - restrict: Remove specified operations from all rules in the policy
        - disable:  Delete all rules from the policy (effectively disabling it)
    """
    overrides = cfg.get("default_policy_overrides", {})
    if not overrides:
        return

    header("0. Restricting Default Policies")

    for policy_name, override in overrides.items():
        action = override.get("action", "restrict")

        # Fetch the current policy with its rules
        data = client.get(f"/policies/name/{policy_name}?fields=rules")
        if not data.get("id"):
            warn(f"{policy_name} not found — skipping")
            continue

        pid = data["id"]
        current_rules = data.get("rules", [])

        if action == "disable":
            # Remove all rules — policy exists but does nothing
            client.patch(f"/policies/{pid}", [
                {"op": "replace", "path": "/rules", "value": []}
            ])
            log(f"{policy_name} disabled (all rules removed)")

        elif action == "restrict":
            remove_ops = set(override.get("remove_operations", []))
            if not remove_ops:
                warn(f"{policy_name} restrict: no operations specified")
                continue

            new_rules = []
            for rule in current_rules:
                original_ops = rule.get("operations", [])
                filtered_ops = [op for op in original_ops if op not in remove_ops]

                if not filtered_ops:
                    # Rule has no operations left — drop it entirely
                    warn(f"  {rule.get('name', '?')}: all operations removed — rule dropped")
                    continue

                rule["operations"] = filtered_ops
                new_rules.append(rule)

            # PATCH the updated rules
            client.patch(f"/policies/{pid}", [
                {"op": "replace", "path": "/rules", "value": new_rules}
            ])

            removed = ", ".join(sorted(remove_ops))
            log(f"{policy_name}: removed [{removed}] from rules")

        else:
            warn(f"{policy_name}: unknown action '{action}' — skipping")


def provision_domains(client: OMDClient, cfg: dict) -> None:
    header("1. Creating Domains")
    for d in cfg.get("domains", []):
        client.post("/domains", d)
        log(f"{d['name']} domain ready")


def provision_policies(client: OMDClient, cfg: dict) -> None:
    header("2. Creating Policies")
    for p in cfg.get("policies", []):
        create_or_update_policy(client, p)


def provision_roles(client: OMDClient, cfg: dict) -> None:
    header("3. Creating Roles")
    for r in cfg.get("roles", []):
        policy_csv = ",".join(r["policies"])
        create_or_update_role(client, r["name"], policy_csv, r["description"])


def generate_teams_from_domains(cfg: dict) -> list[dict]:
    """Auto-generate the standard team structure from domains.

    When 'auto_teams' is true in the config, generates for each domain:
      - {Domain}Team (BusinessUnit) — parent team for the domain
      - {Domain}Readers (Group) — DomainReader role, child of {Domain}Team
      - {Domain}Writers (Group) — DomainWriter role, child of {Domain}Team
      - {Domain}Admins (Group)  — DomainAdmin role, child of {Domain}Team

    Plus a global DefaultReaders group with the DomainReader role (no domain).

    The role names can be customised via 'auto_teams_roles':
        "auto_teams_roles": {"reader": "DomainReader", "writer": "DomainWriter", "admin": "DomainAdmin"}
    """
    role_map = cfg.get("auto_teams_roles", {
        "reader": "DomainReader",
        "writer": "DomainWriter",
        "admin": "DomainAdmin",
    })

    teams: list[dict] = []

    for d in cfg.get("domains", []):
        domain_name = d["name"]
        display = d.get("displayName", domain_name.title())

        # Derive CamelCase prefix from displayName (e.g. "Market Impact" -> "MarketImpact")
        prefix = display.replace(" ", "")

        # Parent BusinessUnit
        teams.append({
            "name": f"{prefix}Team",
            "displayName": f"{display} Team",
            "teamType": "BusinessUnit",
            "domain": domain_name,
        })

        # Reader / Writer / Admin groups
        for tier, suffix in [("reader", "Readers"), ("writer", "Writers"), ("admin", "Admins")]:
            role = role_map.get(tier, "")
            teams.append({
                "name": f"{prefix}{suffix}",
                "displayName": f"{display} {suffix}",
                "teamType": "Group",
                "parent": f"{prefix}Team",
                "role": role,
                "domain": domain_name,
            })

    # Global DefaultReaders (no domain — simulates a new SSO user's landing spot)
    teams.append({
        "name": "DefaultReaders",
        "displayName": "Default Readers",
        "teamType": "Group",
        "role": role_map.get("reader", "DomainReader"),
    })

    return teams


def provision_teams(client: OMDClient, cfg: dict) -> None:
    header("4. Creating Teams")

    # Use auto-generated teams if auto_teams is enabled, otherwise use explicit list
    if cfg.get("auto_teams", False):
        teams = generate_teams_from_domains(cfg)
        log(f"auto_teams: generated {len(teams)} teams from {len(cfg.get('domains', []))} domains")
    else:
        teams = cfg.get("teams", [])

    # Merge any extra teams defined explicitly (e.g. cross-domain groups)
    extra_teams = cfg.get("extra_teams", [])
    if extra_teams:
        teams = teams + extra_teams
        log(f"  + {len(extra_teams)} extra teams from config")

    # Pass 1: Create all teams (without parent/role/domain — those need IDs)
    for t in teams:
        name = t["name"]
        team_type = t["teamType"]
        display = t.get("displayName", name)

        client.post("/teams", {
            "name": name,
            "displayName": display,
            "description": name,
            "teamType": team_type,
        })

    # Pass 2: Assign parent, role, domain via PUT (idempotent, correct field formats)
    for t in teams:
        name = t["name"]
        team_type = t["teamType"]
        display = t.get("displayName", name)

        put_body: dict = {
            "name": name,
            "displayName": display,
            "teamType": team_type,
        }

        # Parent — PUT expects UUID array
        parent = t.get("parent", "")
        if parent:
            pid = client.extract_id(client.get(f"/teams/name/{parent}"))
            if pid:
                put_body["parents"] = [pid]

        # Role — PUT expects UUID array
        role = t.get("role", "")
        if role:
            rid = client.extract_id(client.get(f"/roles/name/{role}"))
            if rid:
                put_body["defaultRoles"] = [rid]

        # Domain — PUT expects FQN string array
        domain = t.get("domain", "")
        if domain:
            put_body["domains"] = [domain]

        client.put("/teams", put_body)
        log(f"{name} ({team_type}) ready")


def provision_users(client: OMDClient, cfg: dict) -> None:
    header("5. Creating Users")
    for u in cfg.get("users", []):
        name = u["name"]
        email = u["email"]
        team = u["team"]
        password = u["password"]
        display = u.get("displayName", "")

        tid = client.extract_id(client.get(f"/teams/name/{team}"))
        team_ids = [tid] if tid else []

        uid = client.create_user_with_login(
            name=name,
            email=email,
            password=password,
            display_name=display,
            team_ids=team_ids,
        )

        if uid:
            log(f"{name} ({email}) -> team {team}")
        else:
            err(f"Failed to create {name} ({email})")


def provision_glossaries(client: OMDClient, cfg: dict) -> None:
    header("6. Creating Glossaries & Terms")
    for g in cfg.get("glossaries", []):
        name = g["name"]
        domain = g.get("domain", "")

        # Create glossary (POST for initial creation)
        client.post("/glossaries", {
            "name": name,
            "displayName": g.get("displayName", name),
            "description": g.get("description", ""),
            "mutuallyExclusive": False,
        })

        gid = client.extract_id(client.get(f"/glossaries/name/{name}"))
        if not gid:
            err(f"Cannot find glossary {name}")
            continue

        # Domain — use PUT with 'domains' (plural, FQN string array)
        if domain:
            client.put("/glossaries", {
                "name": name,
                "displayName": g.get("displayName", name),
                "description": g.get("description", ""),
                "domains": [domain],
            })

        # Owner team — use PATCH (works in both 1.11.x and 1.12.x)
        owner_team = g.get("owner_team", "")
        if owner_team:
            otid = client.extract_id(client.get(f"/teams/name/{owner_team}"))
            if otid:
                client.patch(f"/glossaries/{gid}", [
                    {"op": "add", "path": "/owners/0", "value": {"id": otid, "type": "team"}}
                ])

        # Reviewer team
        reviewer_team = g.get("reviewer_team", "")
        if reviewer_team:
            rtid = client.extract_id(client.get(f"/teams/name/{reviewer_team}"))
            if rtid:
                client.patch(f"/glossaries/{gid}", [
                    {"op": "add", "path": "/reviewers/0", "value": {"id": rtid, "type": "team"}}
                ])

        log(f"{name} glossary ready (domain={domain or 'none'})")

        # Terms — 'glossary' field expects FQN string (not object)
        for t in g.get("terms", []):
            client.put("/glossaryTerms", {
                "glossary": name,
                "name": t["name"],
                "displayName": t.get("displayName", t["name"]),
                "description": t.get("description", ""),
            })
            log(f"  +-- {t['name']}")


# ── Verification ────────────────────────────────────────────────────────

def verify_setup(client: OMDClient, cfg: dict) -> None:
    header("7. Verification")

    for g in cfg.get("glossaries", []):
        data = client.get(f"/glossaries/name/{g['name']}?fields=domains,owners,reviewers")
        domains = [x["name"] for x in data.get("domains", [])]
        owners = [x["name"] for x in data.get("owners", [])]
        reviewers = [x["name"] for x in data.get("reviewers", [])]
        print(f"  {data.get('name', g['name'])}: domains={domains} owners={owners} reviewers={reviewers}")

    print()
    log("Active policies:")
    for p in cfg.get("policies", []):
        data = client.get(f"/policies/name/{p['name']}?fields=rules")
        print(f"  {data.get('name', p['name'])}:")
        for r in data.get("rules", []):
            ops = ", ".join(r["operations"][:3]) + ("..." if len(r["operations"]) > 3 else "")
            cond = r.get("condition", "none")
            print(f"    {r['name']}: {r['effect']} [{ops}] condition={cond}")


def print_summary(cfg: dict, config_path: str) -> None:
    server = cfg["server"]
    auth_type = server.get("auth_type", "basic")
    base_url = server["base_url"]

    print(f"\n{BOLD}{'=' * 56}{NC}")
    print(f"{BOLD}  RBAC Setup Complete{NC}")
    print(f"{BOLD}{'=' * 56}{NC}")
    print(f"\n  CONFIG:    {config_path}")
    print(f"  SERVER:    {base_url}")
    print(f"  AUTH:      {auth_type}")
    print(f"  OMD VER:   {cfg.get('omd_version', 'N/A')}")

    print("\n  DOMAINS:")
    for d in cfg.get("domains", []):
        print(f"    {d['name']}")

    print("\n  POLICIES:")
    for p in cfg.get("policies", []):
        print(f"    {p['name']}: {p['description'][:70]}...")

    print("\n  ROLES:")
    for r in cfg.get("roles", []):
        print(f"    {r['name']} -> [{', '.join(r['policies'])}]")

    print("\n  USERS:")
    for u in cfg.get("users", []):
        print(f"    {u['email']:40s} team={u['team']}")

    print("\n  GLOSSARIES:")
    for g in cfg.get("glossaries", []):
        terms = [t["name"] for t in g.get("terms", [])]
        print(f"    {g['name']} (domain={g.get('domain', 'N/A')}) terms={terms}")

    ui_url = base_url.replace("/api/v1", "")
    print(f"\n  OpenMetadata UI: {ui_url}\n")


# ── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Config-driven RBAC provisioner for OpenMetadata",
    )
    parser.add_argument(
        "--config", "-c",
        default="config/keytrade-glossary.json",
        help="Path to JSON config file (default: config/keytrade-glossary.json)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        err(f"Config not found: {config_path}")
        sys.exit(1)

    cfg = json.loads(config_path.read_text())
    server = cfg["server"]

    print(f"{BOLD}OpenMetadata RBAC Setup{NC}")
    print(f"Config: {config_path}")
    print(f"Auth:   {server.get('auth_type', 'basic')}\n")

    # Wait for server
    print(f"Waiting for OpenMetadata at {server['base_url']} ...")
    client = None
    for _ in range(30):
        try:
            client = OMDClient(
                base_url=server["base_url"],
                auth_type=server.get("auth_type", "basic"),
                admin_email=server.get("admin_email", ""),
                admin_password=server.get("admin_password", ""),
                api_token=server.get("api_token", ""),
            )
            if client.ping():
                break
        except Exception:
            pass
        time.sleep(2)

    if client is None or not client.ping():
        err("Server not reachable after 60 s")
        sys.exit(1)

    log(f"Server is up! (auth={client.auth_type})")

    restrict_default_policies(client, cfg)
    provision_domains(client, cfg)
    provision_policies(client, cfg)
    provision_roles(client, cfg)
    provision_teams(client, cfg)
    provision_users(client, cfg)
    provision_glossaries(client, cfg)
    verify_setup(client, cfg)
    print_summary(cfg, str(config_path))


if __name__ == "__main__":
    main()
