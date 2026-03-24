"""Lightweight OpenMetadata API client using httpx.

Supports two auth modes:
  - basic:  email + password login (local Docker / self-hosted)
  - token:  pre-existing JWT or API token (Collate cloud)

Environment variables always override config values:
  OMD_BASE_URL, OMD_AUTH_TYPE, OMD_ADMIN_EMAIL,
  OMD_ADMIN_PASSWORD, OMD_API_TOKEN
"""

from __future__ import annotations

import base64
import os
import subprocess
import json as _json

import httpx

_TIMEOUT = 30.0


class OMDClient:
    """Thin REST client for the OpenMetadata API."""

    def __init__(
        self,
        base_url: str,
        auth_type: str = "basic",
        admin_email: str = "",
        admin_password: str = "",
        api_token: str = "",
    ):
        self.base_url = os.environ.get("OMD_BASE_URL", base_url).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=_TIMEOUT)
        self._token_cache: dict[str, str] = {}

        # Resolve auth — env vars win
        self.auth_type = os.environ.get("OMD_AUTH_TYPE", auth_type) or "basic"
        env_token = os.environ.get("OMD_API_TOKEN", "")

        if self.auth_type == "token" or env_token:
            self.admin_token = env_token or api_token
            if not self.admin_token:
                raise RuntimeError(
                    "auth_type=token but no token provided "
                    "(set OMD_API_TOKEN or server.api_token in config)"
                )
        else:
            email = os.environ.get("OMD_ADMIN_EMAIL", admin_email)
            pwd = os.environ.get("OMD_ADMIN_PASSWORD", admin_password)
            self.admin_token = self._login(email, pwd)
            if not self.admin_token:
                raise RuntimeError("Cannot authenticate as admin — check credentials")

    # ── Auth ────────────────────────────────────────────────────────────
    def _login(self, email: str, password: str) -> str:
        if email in self._token_cache:
            return self._token_cache[email]
        pwd_b64 = base64.b64encode(password.encode()).decode()
        resp = self._http.post(
            "/users/login",
            json={"email": email, "password": pwd_b64},
        )
        token = resp.json().get("accessToken", "")
        self._token_cache[email] = token
        return token

    def get_user_token(self, email: str, password: str) -> str:
        return self._login(email, password)

    # ── User provisioning (basic auth) ──────────────────────────────────
    def create_user_with_login(
        self,
        name: str,
        email: str,
        password: str,
        display_name: str = "",
        team_ids: list[str] | None = None,
    ) -> str:
        """Create a user with working login credentials.

        Strategy:
        1. Admin PUT to create user (with teams)
        2. Bcrypt-hash the password and set authenticationMechanism
           directly in the DB via Docker exec (same pattern as admin user)
        3. Mark email as verified in the DB

        Returns the user ID or empty string on failure.
        """
        # Step 1: Try to delete existing user first (idempotent)
        existing = self.get(f"/users/name/{name}")
        if existing.get("id"):
            self._http.delete(
                f"/users/{existing['id']}?hardDelete=true",
                headers=self._headers(),
            )

        # Step 2: Create user via admin PUT
        body: dict = {
            "name": name,
            "displayName": display_name or name,
            "email": email,
            "isBot": False,
            "isAdmin": False,
        }
        if team_ids:
            body["teams"] = team_ids
        resp = self.put("/users", body)
        if resp.status_code not in (200, 201):
            return ""

        user_id = resp.json().get("id", "")

        # Step 3: Set authenticationMechanism + isEmailVerified in DB
        self._set_basic_auth_in_db(email, password)

        return user_id

    def _set_basic_auth_in_db(self, email: str, password: str) -> bool:
        """Set bcrypt password hash and email verification directly in the DB.

        Uses the same authenticationMechanism JSON structure as the admin user:
        {"config": {"password": "$2a$12$..."}, "authType": "BASIC"}
        """
        import bcrypt

        container, db_type = self._find_db_container()
        if not container:
            return False

        # Bcrypt-hash the password (same as OMD does internally)
        pwd_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
        # Escape single quotes for SQL
        pwd_hash_escaped = pwd_hash.replace("'", "''")

        auth_json = (
            '{"config": {"password": "' + pwd_hash_escaped + '"}, "authType": "BASIC"}'
        )

        try:
            if db_type == "postgres":
                sql = (
                    f"UPDATE user_entity SET json = jsonb_set("
                    f"jsonb_set(json::jsonb, '{{authenticationMechanism}}', '{auth_json}'::jsonb), "
                    f"'{{isEmailVerified}}', 'true'"
                    f")::json "
                    f"WHERE json::jsonb->>'email' = '{email}'"
                )
                result = subprocess.run(
                    ["docker", "exec", container, "psql", "-U", "openmetadata_user",
                     "-d", "openmetadata_db", "-c", sql],
                    capture_output=True, text=True, timeout=10,
                )
                return result.returncode == 0
            else:
                # MySQL
                sql = (
                    f"UPDATE openmetadata_db.user_entity "
                    f"SET json = JSON_SET(json, "
                    f"'$.authenticationMechanism', CAST('{auth_json}' AS JSON), "
                    f"'$.isEmailVerified', true) "
                    f"WHERE json->>'$.email' = '{email}'"
                )
                subprocess.run(
                    ["docker", "exec", container, "mysql", "-u", "openmetadata_user",
                     "-popenmetadata_password", "-e", sql],
                    capture_output=True, text=True, timeout=10,
                )
                return True

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _find_db_container(self) -> tuple[str, str]:
        """Find the running OMD database Docker container (PostgreSQL or MySQL).

        Returns (container_name, db_type) where db_type is 'postgres' or 'mysql'.
        Returns ('', '') if not found.
        """
        try:
            # Check for PostgreSQL containers first
            for image_filter in ["postgres", "mysql"]:
                result = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) != 2:
                        continue
                    name, image = parts
                    if "postgres" in image.lower():
                        return (name, "postgres")
                    if "mysql" in image.lower():
                        return (name, "mysql")
                break  # Only need to run docker ps once
            return ("", "")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ("", "")

    # ── Generic REST helpers ────────────────────────────────────────────
    def _headers(self, token: str | None = None, patch: bool = False) -> dict:
        t = token or self.admin_token
        ct = "application/json-patch+json" if patch else "application/json"
        return {"Content-Type": ct, "Authorization": f"Bearer {t}"}

    def get(self, path: str, token: str | None = None) -> dict:
        r = self._http.get(path, headers=self._headers(token))
        return r.json() if r.status_code < 400 else {}

    def post(self, path: str, body: dict | str, token: str | None = None) -> httpx.Response:
        h = self._headers(token)
        if isinstance(body, str):
            return self._http.post(path, content=body, headers=h)
        return self._http.post(path, json=body, headers=h)

    def put(self, path: str, body: dict, token: str | None = None) -> httpx.Response:
        return self._http.put(path, json=body, headers=self._headers(token))

    def patch(self, path: str, ops: list[dict], token: str | None = None) -> httpx.Response:
        return self._http.patch(path, json=ops, headers=self._headers(token, patch=True))

    def delete(self, path: str, token: str | None = None) -> httpx.Response:
        return self._http.delete(path, headers=self._headers(token))

    # ── Resource helpers ────────────────────────────────────────────────
    def extract_id(self, data: dict) -> str:
        return data.get("id", "")

    def resolve_resource_id(self, resource_type: str, resource_name: str) -> str:
        endpoints = {
            "glossary": f"/glossaries/name/{resource_name}",
            "glossaryTerm": f"/glossaryTerms/name/{resource_name}",
            "table": f"/tables/name/{resource_name}",
            "database": f"/databases/name/{resource_name}",
            "pipeline": f"/pipelines/name/{resource_name}",
            "dashboard": f"/dashboards/name/{resource_name}",
            "topic": f"/topics/name/{resource_name}",
        }
        path = endpoints.get(resource_type)
        if not path:
            return ""
        return self.extract_id(self.get(path))

    def get_permissions(self, token: str, resource_type: str, resource_id: str) -> dict:
        resp = self.get(f"/permissions/{resource_type}/{resource_id}", token=token)
        perm_map: dict[str, dict] = {}
        for p in resp.get("permissions", []):
            rule = p.get("rule", {})
            perm_map[p["operation"]] = {
                "access": p["access"],
                "policy": p.get("policy", "N/A"),
                "rule": rule.get("name", "N/A") if isinstance(rule, dict) else "N/A",
            }
        return perm_map

    # ── Server health ───────────────────────────────────────────────────
    def ping(self) -> bool:
        """Return True if the OMD server responds to /system/version."""
        try:
            r = self._http.get("/system/version", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False
