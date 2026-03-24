"""OpenMetadata RBAC Permission Matrix Test Runner.

Reads a JSON config with test_matrix definitions, authenticates as each user,
queries the OMD permissions API, and compares actual vs expected access.
Produces a pass/fail report for every role x resource x operation combo.

Usage (via uv):
    uv run omd-test --config config/example-glossary-governance.json
    uv run omd-test --config config/example-glossary-governance.json --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from omd_rbac.client import OMDClient

# ── ANSI colours ────────────────────────────────────────────────────────
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"


def access_matches(expected: str, actual: str) -> bool:
    """Compare expected vs actual, treating deny <-> notAllow as equivalent."""
    if expected == actual:
        return True
    deny_variants = {"deny", "notAllow"}
    return expected in deny_variants and actual in deny_variants


def run_tests(config_path: str, report_path: str, verbose: bool = False) -> int:
    """Run the full test matrix. Returns exit code (0=all pass, 1=failures)."""

    cfg = json.loads(Path(config_path).read_text())
    server = cfg["server"]
    base_url = server["base_url"]

    print(f"{BOLD}{'=' * 58}{NC}")
    print(f"{BOLD}  OpenMetadata RBAC Permission Matrix Test Harness{NC}")
    print(f"{BOLD}{'=' * 58}{NC}")
    print()
    print(f"  Config:  {DIM}{config_path}{NC}")
    print(f"  Server:  {DIM}{base_url}{NC}")
    print(f"  Report:  {DIM}{report_path}{NC}")
    print()

    # Connect (supports basic + token auth, env var overrides)
    client = OMDClient(
        base_url=base_url,
        auth_type=server.get("auth_type", "basic"),
        admin_email=server.get("admin_email", ""),
        admin_password=server.get("admin_password", ""),
        api_token=server.get("api_token", ""),
    )
    print(f"{GREEN}[+]{NC} Admin authenticated (mode={client.auth_type})")

    # Build password map
    user_passwords = {u["email"]: u["password"] for u in cfg.get("users", [])}

    test_matrix = cfg.get("test_matrix", [])
    total_assertions = sum(len(t.get("expect", {})) for t in test_matrix)
    print(f"{GREEN}[+]{NC} {total_assertions} assertions across {len(test_matrix)} scenarios")
    print()

    # ── Execute tests ──────────────────────────────────────────────────
    results: list[dict] = []
    total = passed = failed = skipped = 0

    for i, test in enumerate(test_matrix):
        test_name = test["name"]
        user_email = test["user"]
        resource_type = test["resource_type"]
        resource_name = test["resource"]
        expectations = test["expect"]

        print(f"{BOLD}{BLUE}--- Test {i}: {test_name} ---{NC}")
        print(f"  User: {user_email}  Resource: {resource_type}/{resource_name}")

        # Authenticate user
        pwd = user_passwords.get(user_email, "Test@12345")
        user_token = client.get_user_token(user_email, pwd)
        if not user_token:
            print(f"  {RED}SKIP — cannot authenticate {user_email}{NC}")
            for op, exp in expectations.items():
                skipped += 1
                results.append({
                    "test": test_name, "user": user_email,
                    "resource_type": resource_type, "resource": resource_name,
                    "operation": op, "expected": exp, "actual": "NO_TOKEN",
                    "policy": "N/A", "rule": "N/A", "result": "skip",
                })
            print()
            continue

        # Resolve resource ID
        resource_id = client.resolve_resource_id(resource_type, resource_name)
        if not resource_id:
            print(f"  {RED}SKIP — resource '{resource_name}' not found{NC}")
            for op, exp in expectations.items():
                skipped += 1
                results.append({
                    "test": test_name, "user": user_email,
                    "resource_type": resource_type, "resource": resource_name,
                    "operation": op, "expected": exp, "actual": "NOT_FOUND",
                    "policy": "N/A", "rule": "N/A", "result": "skip",
                })
            print()
            continue

        # Fetch permissions once per scenario
        perm_map = client.get_permissions(user_token, resource_type, resource_id)

        for op, expected in expectations.items():
            total += 1
            actual_info = perm_map.get(op, {"access": "unknown", "policy": "N/A", "rule": "N/A"})
            actual = actual_info["access"]
            match = access_matches(expected, actual)

            if match:
                passed += 1
                status = "pass"
                print(f"  {GREEN}PASS{NC}  {op:25s}  expected={expected:10s}  actual={actual}")
            else:
                failed += 1
                status = "fail"
                print(f"  {RED}FAIL{NC}  {op:25s}  expected={expected:10s}  actual={actual}")

            if verbose and (actual_info["policy"] != "N/A" or actual_info["rule"] != "N/A"):
                print(f"  {DIM}      policy={actual_info['policy']}  rule={actual_info['rule']}{NC}")

            results.append({
                "test": test_name, "user": user_email,
                "resource_type": resource_type, "resource": resource_name,
                "operation": op, "expected": expected, "actual": actual,
                "policy": actual_info["policy"], "rule": actual_info["rule"],
                "result": status,
            })

        print()

    # ── Console summary ────────────────────────────────────────────────
    print(f"{BOLD}{'=' * 58}{NC}")
    print(f"{BOLD}  RBAC TEST RESULTS SUMMARY{NC}")
    print(f"{BOLD}{'=' * 58}{NC}")
    print()

    by_test: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_test[r["test"]].append(r)

    for tname, items in by_test.items():
        all_pass = all(i["result"] == "pass" for i in items)
        icon = f"{GREEN}+{NC}" if all_pass else f"{RED}x{NC}"
        print(f"  {icon} {tname}")
        for item in items:
            if item["result"] == "pass":
                c, marker = GREEN, "PASS"
            elif item["result"] == "fail":
                c, marker = RED, "FAIL"
            else:
                c, marker = YELLOW, "SKIP"
            print(f"      {c}{marker}{NC}  {item['operation']:25s}  "
                  f"expected={item['expected']:10s}  actual={item['actual']}")
        print()

    pct = (passed / total * 100) if total > 0 else 0
    print(f"  Total: {total}   {GREEN}Passed: {passed}{NC}   "
          f"{RED}Failed: {failed}{NC}   {YELLOW}Skipped: {skipped}{NC}")

    if failed == 0 and skipped == 0:
        print(f"\n  {GREEN}{'=' * 50}")
        print(f"  ALL {total} ASSERTIONS PASSED ({pct:.0f}%)")
        print(f"  {'=' * 50}{NC}")
    elif failed > 0:
        print(f"\n  {RED}{'=' * 50}")
        print(f"  {failed} ASSERTION(S) FAILED")
        print(f"  {'=' * 50}{NC}")

    # ── Write JSON report ──────────────────────────────────────────────
    report = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": config_path,
            "server": base_url,
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": f"{pct:.1f}%",
        },
        "results": results,
    }

    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2))

    print(f"\n  Report saved: {report_path}\n")
    return 1 if failed > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="OMD RBAC Permission Matrix Tester")
    parser.add_argument("--config", "-c", required=True, help="Path to JSON config file")
    parser.add_argument("--report", "-r", default="", help="Path for JSON report output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show policy/rule details")
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"{RED}Config not found: {args.config}{NC}")
        sys.exit(1)

    report_path = args.report
    if not report_path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = f"reports/results-{ts}.json"

    sys.exit(run_tests(args.config, report_path, args.verbose))


if __name__ == "__main__":
    main()
