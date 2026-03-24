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
        1. Self-signup via POST /users/signup (creates basic_auth entry)
        2. Confirm email via Docker exec into the MySQL DB (extracts token)
        3. Assign teams via admin PATCH
        4. Returns the user ID

        If signup fails (user exists), falls back to admin PUT + changePassword.
        If Docker DB access fails, falls back gracefully (user created but login may not work).
        """
        pwd_b64 = base64.b64encode(password.encode()).decode()

        # Step 1: Try to delete existing user first (idempotent)
        existing = self.get(f"/users/name/{name}")
        if existing.get("id"):
            self._http.delete(
                f"/users/{existing['id']}?hardDelete=true",
                headers=self._headers(),
            )

        # Step 2: Signup (creates basic_auth_mechanism entry in DB)
        first_name = display_name.split()[0] if display_name else name
        last_name = display_name.split()[-1] if display_name and len(display_name.split()) > 1 else "User"
        signup_resp = self._http.post(
            "/users/signup",
            json={
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "password": pwd_b64,
            },
        )

        if signup_resp.status_code not in (200, 201):
            # Fallback: admin PUT
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
            return self.extract_id(resp.json()) if resp.status_code in (200, 201) else ""

        user_data = signup_resp.json()
        user_id = user_data.get("id", "")

        # Step 3: Confirm email via Docker DB
        self._confirm_email_via_docker(email)

        # Step 4: Assign teams via admin PATCH (signup doesn't set teams)
        if team_ids and user_id:
            self.patch(f"/users/{user_id}", [
                {"op": "replace", "path": "/teams", "value": [{"id": tid, "type": "team"} for tid in team_ids]}
            ])

        # Step 5: Set display name if different from signup default
        if display_name and user_id:
            self.patch(f"/users/{user_id}", [
                {"op": "replace", "path": "/displayName", "value": display_name}
            ])

        return user_id

    def _confirm_email_via_docker(self, email: str) -> bool:
        """Confirm a user's email by extracting the verification token from the OMD MySQL DB via Docker.

        This is necessary because OMD basic auth requires email confirmation before login,
        and Docker environments typically don't have SMTP configured.
        """
        # Find the OMD MySQL container
        container = self._find_mysql_container()
        if not container:
            return False

        try:
            # Query the token_relation table for the email verification token
            sql = (
                f"SELECT token FROM openmetadata_db.token_relation "
                f"WHERE userid = (SELECT id FROM openmetadata_db.user_entity WHERE json->>'$.email' = '{email}') "
                f"AND tokenType = 'EMAIL_VERIFICATION' "
                f"ORDER BY expiryDate DESC LIMIT 1"
            )
            result = subprocess.run(
                ["docker", "exec", container, "mysql", "-u", "openmetadata_user",
                 "-popenmetadata_password", "-N", "-e", sql],
                capture_output=True, text=True, timeout=10,
            )

            token = result.stdout.strip()
            if not token:
                # Try alternative: directly update the user entity to mark email as verified
                sql_update = (
                    f"UPDATE openmetadata_db.user_entity "
                    f"SET json = JSON_SET(json, '$.isEmailVerified', true) "
                    f"WHERE json->>'$.email' = '{email}'"
                )
                subprocess.run(
                    ["docker", "exec", container, "mysql", "-u", "openmetadata_user",
                     "-popenmetadata_password", "-e", sql_update],
                    capture_output=True, text=True, timeout=10,
                )
                return True

            # Confirm via the API
            resp = self._http.put(
                f"/users/registrationConfirmation?user={token}",
            )
            return resp.status_code == 200

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _find_mysql_container(self) -> str:
        """Find the running OMD MySQL Docker container name."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "ancestor=mysql:8", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
            containers = result.stdout.strip().split("\n")
            # Look for openmetadata-related container
            for c in containers:
                if c and ("openmetadata" in c.lower() or "mysql" in c.lower()):
                    return c
            # Return first MySQL container if any
            return containers[0] if containers and containers[0] else ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

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
