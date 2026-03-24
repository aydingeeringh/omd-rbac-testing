"""Preflight checks for the OMD RBAC testing environment.

Verifies that all prerequisites are installed and reachable:
  - Python >= 3.9
  - Docker & Docker Compose
  - curl
  - uv (optional but recommended)
  - OMD server connectivity (if --server flag used)

Usage (via uv):
    uv run omd-check
    uv run omd-check --server http://localhost:8585/api/v1
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys

# ── ANSI colours ────────────────────────────────────────────────────────
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

ALL_OK = True


def check(name: str, found: bool, version: str = "", required: bool = True) -> None:
    global ALL_OK
    if found:
        suffix = f" ({version})" if version else ""
        print(f"  {GREEN}[+]{NC} {name}{suffix}")
    elif required:
        ALL_OK = False
        print(f"  {RED}[x]{NC} {name} — NOT FOUND (required)")
    else:
        print(f"  {YELLOW}[~]{NC} {name} — not found (optional)")


def get_version(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip().split("\n")[0]
    except Exception:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for OMD RBAC testing")
    parser.add_argument("--server", default="", help="OMD server URL to test connectivity")
    args = parser.parse_args()

    print(f"\n{BOLD}OMD RBAC Testing — Preflight Checks{NC}")
    print(f"  Platform: {platform.system()} {platform.machine()}")
    print(f"  Python:   {sys.version.split()[0]}")
    print()

    # Python >= 3.9
    py_ok = sys.version_info >= (3, 9)
    check("Python >= 3.9", py_ok, f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    # curl
    curl_path = shutil.which("curl")
    curl_ver = get_version(["curl", "--version"]) if curl_path else ""
    check("curl", bool(curl_path), curl_ver.split()[1] if len(curl_ver.split()) > 1 else "")

    # Docker
    docker_path = shutil.which("docker")
    docker_ver = get_version(["docker", "--version"]) if docker_path else ""
    check("Docker", bool(docker_path), docker_ver)

    # Docker Compose (v2 plugin or standalone)
    compose_ok = False
    compose_ver = ""
    if docker_path:
        compose_ver = get_version(["docker", "compose", "version"])
        compose_ok = bool(compose_ver)
    if not compose_ok:
        dc_path = shutil.which("docker-compose")
        if dc_path:
            compose_ver = get_version(["docker-compose", "--version"])
            compose_ok = True
    check("Docker Compose", compose_ok, compose_ver)

    # uv
    uv_path = shutil.which("uv")
    uv_ver = get_version(["uv", "--version"]) if uv_path else ""
    check("uv", bool(uv_path), uv_ver, required=False)

    # httpx (Python dependency)
    try:
        import httpx  # noqa: F401
        httpx_ver = httpx.__version__
        check("httpx (Python package)", True, httpx_ver)
    except ImportError:
        check("httpx (Python package)", False)
        print(f"       {DIM}Install with: uv sync  or  pip install httpx{NC}")

    # Git (optional)
    git_path = shutil.which("git")
    git_ver = get_version(["git", "--version"]) if git_path else ""
    check("git", bool(git_path), git_ver, required=False)

    # Server connectivity
    if args.server:
        print()
        print(f"{BOLD}  Server connectivity:{NC}")
        try:
            import httpx as hx
            r = hx.get(f"{args.server.rstrip('/')}/system/version", timeout=10)
            if r.status_code == 200:
                ver_data = r.json()
                omd_ver = ver_data.get("version", "unknown")
                check(f"OMD server at {args.server}", True, f"v{omd_ver}")
            else:
                check(f"OMD server at {args.server}", False)
        except Exception as e:
            check(f"OMD server at {args.server}", False)
            print(f"       {DIM}{e}{NC}")

    # Summary
    print()
    if ALL_OK:
        print(f"  {GREEN}{BOLD}All required checks passed!{NC}")
    else:
        print(f"  {RED}{BOLD}Some required checks failed — see above.{NC}")
        sys.exit(1)

    print()


if __name__ == "__main__":
    main()
