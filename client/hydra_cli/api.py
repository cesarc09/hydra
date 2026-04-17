"""Thin HTTP client for the Hydra REST API. Uses only stdlib."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:8400"


def _base_url() -> str:
    return os.environ.get("HYDRA_URL", DEFAULT_URL).rstrip("/")


def _token() -> str:
    return os.environ.get("HYDRA_AUTH_TOKEN", "")


def _request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, str]:
    """Make an HTTP request to the Hydra API. Returns (status_code, response_body)."""
    url = f"{_base_url()}{path}"
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "hydra-cli/0.1")
    token = _token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    instance_id = os.environ.get("HYDRA_INSTANCE_ID", "").strip()
    if instance_id:
        req.add_header("X-Instance-Id", instance_id)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def get(path: str) -> tuple[int, str]:
    return _request("GET", path)


def post(path: str, payload: dict) -> tuple[int, str]:
    return _request("POST", path, body=json.dumps(payload).encode("utf-8"))


def put_json(path: str, payload: dict) -> tuple[int, str]:
    return _request("PUT", path, body=json.dumps(payload).encode("utf-8"))


def put_text(path: str, text: str) -> tuple[int, str]:
    return _request(
        "PUT", path, body=text.encode("utf-8"), content_type="text/plain"
    )


def delete(path: str) -> tuple[int, str]:
    return _request("DELETE", path)
