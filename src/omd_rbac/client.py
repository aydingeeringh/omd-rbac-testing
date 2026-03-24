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
        return self._http.patch(path, json=ops, headers=self._headers(patch=True))

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
