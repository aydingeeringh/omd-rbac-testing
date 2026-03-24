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


def provision_teams(client: OMDClient, cfg: dict) -> None:
    header("4. Creating Teams")
    for t in cfg.get("teams", []):
        name = t["name"]
        team_type = t["teamType"]
        display = t.get("displayName", name)

        client.post("/teams", {
            "name": name,
            "displayName": display,
            "description": name,
            "teamType": team_type,
        })

        tid = client.extract_id(client.get(f"/teams/name/{name}"))
        if not tid:
            err(f"Cannot find team {name}")
            continue

        # Parent
        parent = t.get("parent", "")
        if parent:
            pid = client.extract_id(client.get(f"/teams/name/{parent}"))
            if pid:
                client.patch(f"/teams/{tid}", [
                    {"op": "add", "path": "/parents", "value": [{"id": pid, "type": "team"}]}
                ])

        # Role
        role = t.get("role", "")
        if role:
            rid = client.extract_id(client.get(f"/roles/name/{role}"))
            if rid:
                client.patch(f"/teams/{tid}", [
                    {"op": "add", "path": "/defaultRoles", "value": [{"id": rid, "type": "role"}]}
                ])

        # Domain
        domain = t.get("domain", "")
        if domain:
            did = client.extract_id(client.get(f"/domains/name/{domain}"))
            if did:
                client.patch(f"/teams/{tid}", [
                    {"op": "add", "path": "/domains", "value": [{"id": did, "type": "domain"}]}
                ])

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

        client.post("/users", {
            "name": name,
            "displayName": display,
            "email": email,
            "isAdmin": False,
            "teams": [tid] if tid else [],
        })

        client.put("/users/changePassword", {
            "username": name,
            "requestType": "USER",
            "newPassword": password,
            "confirmPassword": password,
        })

        log(f"{name} ({email}) -> team {team}")


def provision_glossaries(client: OMDClient, cfg: dict) -> None:
    header("6. Creating Glossaries & Terms")
    for g in cfg.get("glossaries", []):
        name = g["name"]
        domain = g.get("domain", "")

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

        # Domain
        if domain:
            did = client.extract_id(client.get(f"/domains/name/{domain}"))
            if did:
                client.patch(f"/glossaries/{gid}", [
                    {"op": "add", "path": "/domains", "value": [{"id": did, "type": "domain"}]}
                ])

        # Owner team
        owner_team = g.get("owner_team", "")
        if owner_team:
            otid = client.extract_id(client.get(f"/teams/name/{owner_team}"))
            if otid:
                client.patch(f"/glossaries/{gid}", [
                    {"op": "add", "path": "/owners", "value": [{"id": otid, "type": "team"}]}
                ])

        # Reviewer team
        reviewer_team = g.get("reviewer_team", "")
        if reviewer_team:
            rtid = client.extract_id(client.get(f"/teams/name/{reviewer_team}"))
            if rtid:
                client.patch(f"/glossaries/{gid}", [
                    {"op": "add", "path": "/reviewers", "value": [{"id": rtid, "type": "team"}]}
                ])

        log(f"{name} glossary ready (domain={domain or 'none'})")

        # Terms
        for t in g.get("terms", []):
            client.post("/glossaryTerms", {
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
