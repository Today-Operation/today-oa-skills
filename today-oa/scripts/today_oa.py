#!/usr/bin/env python3
"""Public Today OA client using per-user Google Workspace identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_OAUTH_CONFIG = json.loads(
    (SKILL_ROOT / "references" / "oauth-client.json").read_text(encoding="utf-8")
)
LOCAL_RELEASE = json.loads((SKILL_ROOT / "references" / "release.json").read_text(encoding="utf-8"))
API_URL = os.environ.get(
    "TODAY_OA_API_URL",
    str(LOCAL_RELEASE.get("apiBaseUrl", "https://oa-platform-3nj9aw3w.an.gateway.dev")),
).rstrip("/")
GOOGLE_CLIENT_ID = os.environ.get("TODAY_OA_GOOGLE_CLIENT_ID", str(PUBLIC_OAUTH_CONFIG["client_id"]))


def default_state_dir(skill_root: Path = SKILL_ROOT) -> Path:
    community_root = Path("/home/user/.today/skills/community")
    try:
        skill_root.relative_to(community_root)
        return community_root / ".state" / "today-oa"
    except ValueError:
        return Path.home() / ".today-oa"


STATE_DIR = Path(os.environ.get("TODAY_OA_STATE_DIR", str(default_state_dir())))
TOKEN_FILE = STATE_DIR / "auth.json"
DEVICE_FILE = STATE_DIR / "device.json"
CONFIRMATION_FILE = STATE_DIR / "confirmations.json"
UPDATE_CACHE_FILE = STATE_DIR / "update-check.json"
UPDATE_LOCK_FILE = STATE_DIR / "update.lock"
UPDATE_LOG_FILE = STATE_DIR / "update.log"
SCOPES = "openid email profile"
UPDATE_CHECK_TTL_SECONDS = 6 * 60 * 60
UPDATE_MANIFEST_URL = str(LOCAL_RELEASE["updateManifestUrl"])
WRITE_ACTIONS = {
    "create_extra_request",
    "create_and_submit_extra_request",
    "update_request",
    "resubmit_request",
    "submit_request",
    "withdraw_request",
    "request_return",
    "approve_request",
    "reject_request",
    "request_changes",
    "contract_create_draft",
    "contract_update_draft",
    "contract_add_attachment",
    "contract_submit",
    "contract_withdraw",
    "contract_approve",
    "contract_reject",
    "contract_delegate",
}

CONTRACT_ACTIONS = {
    "contract_list_legal_entities",
    "contract_list_my_applications",
    "contract_get_application",
    "contract_get_history",
    "contract_list_pending_approvals",
    *WRITE_ACTIONS.intersection({
        "contract_create_draft",
        "contract_update_draft",
        "contract_add_attachment",
        "contract_submit",
        "contract_withdraw",
        "contract_approve",
        "contract_reject",
        "contract_delegate",
    }),
}
COMMON_APPLICATION_ACTIONS = {
    "list_my_requests",
    "get_request",
    "create_extra_request",
    "create_and_submit_extra_request",
    "update_request",
    "resubmit_request",
    "submit_request",
    "withdraw_request",
    "list_my_approvals",
    "approve_request",
    "reject_request",
    "request_changes",
}
ASSET_REQUEST_BUSINESS_TYPE = "asset_request"
ASSET_REQUEST_FORM_KEY = "asset_request"


class ClientError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_action(action_input: dict[str, Any]) -> str:
    normalized = dict(action_input)
    normalized.pop("confirmationToken", None)
    normalized["confirmed"] = False
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_fingerprint(action_input: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_action(action_input).encode()).hexdigest()


def record_confirmation(token: str, action_input: dict[str, Any]) -> None:
    try:
        confirmations = read_json(CONFIRMATION_FILE)
    except ClientError:
        confirmations = {}
    now = int(time.time())
    current = {
        key: value for key, value in confirmations.items()
        if isinstance(value, dict) and int(value.get("expiresAt", 0)) > now
    }
    current[token] = {
        "fingerprint": action_fingerprint(action_input),
        "expiresAt": now + 15 * 60,
    }
    write_private(CONFIRMATION_FILE, current)


def validate_confirmation(token: str, action_input: dict[str, Any]) -> None:
    try:
        confirmations = read_json(CONFIRMATION_FILE)
    except ClientError:
        raise ClientError("CONFIRMATION_REQUIRED", "Preview this exact action and ask the user to confirm it again") from None
    saved = confirmations.get(token)
    if not isinstance(saved, dict) or int(saved.get("expiresAt", 0)) <= int(time.time()):
        raise ClientError("CONFIRMATION_EXPIRED", "The confirmation expired; preview the action again")
    if not secrets.compare_digest(str(saved.get("fingerprint", "")), action_fingerprint(action_input)):
        raise ClientError("CONFIRMATION_MISMATCH", "The confirmed action differs from the preview")


def consume_confirmation(token: str) -> None:
    try:
        confirmations = read_json(CONFIRMATION_FILE)
    except ClientError:
        return
    confirmations.pop(token, None)
    write_private(CONFIRMATION_FILE, confirmations)


def request_json(url: str, *, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    encoded = None if data is None else urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=encoded, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode())
            if not isinstance(payload, dict):
                raise ClientError("INVALID_RESPONSE", "OA returned an invalid response")
            return payload
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode())
            detail = payload.get("error", payload) if isinstance(payload, dict) else {}
            if isinstance(detail, str):
                code = detail
                message = str(payload.get("error_description", detail))
            elif isinstance(detail, dict):
                code = str(detail.get("code", "HTTP_ERROR"))
                message = str(detail.get("message", "Request failed"))
            else:
                code, message = "HTTP_ERROR", "Request failed"
        except (json.JSONDecodeError, UnicodeDecodeError):
            code, message = "HTTP_ERROR", "Request failed"
        raise ClientError(code, message) from None
    except urllib.error.URLError as error:
        raise ClientError("NETWORK_ERROR", f"Unable to reach OA: {error.reason}") from None


def broker_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-oa-source": "skill"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode())
            detail = body.get("error", body) if isinstance(body, dict) else {}
            code = str(detail.get("code", "GOOGLE_AUTH_FAILED")) if isinstance(detail, dict) else "GOOGLE_AUTH_FAILED"
            message = str(detail.get("message", "Google authentication failed")) if isinstance(detail, dict) else "Google authentication failed"
        except (json.JSONDecodeError, UnicodeDecodeError):
            code, message = "GOOGLE_AUTH_FAILED", "Google authentication failed"
        raise ClientError(code, message) from None
    except urllib.error.URLError as error:
        raise ClientError("NETWORK_ERROR", f"Unable to reach OA: {error.reason}") from None
    if not isinstance(result, dict):
        raise ClientError("INVALID_RESPONSE", "OA returned an invalid authentication response")
    return result


def api_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    headers = {
        "authorization": f"Bearer {active_id_token()}",
        "content-type": "application/json",
        "x-oa-source": "skill",
    }
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode())
            detail = body.get("error", body) if isinstance(body, dict) else {}
            code = str(detail.get("code", "HTTP_ERROR")) if isinstance(detail, dict) else "HTTP_ERROR"
            message = str(detail.get("message", "OA request failed")) if isinstance(detail, dict) else "OA request failed"
        except (json.JSONDecodeError, UnicodeDecodeError):
            code, message = "HTTP_ERROR", "OA request failed"
        raise ClientError(code, message) from None
    except urllib.error.URLError as error:
        raise ClientError("NETWORK_ERROR", f"Unable to reach OA: {error.reason}") from None
    if not isinstance(result, dict):
        raise ClientError("INVALID_RESPONSE", "OA returned an invalid response")
    return result


def device_request_mode() -> str:
    configured = os.environ.get("TODAY_OA_DEVICE_REQUEST_MODE", "auto").strip().lower()
    if configured in {"legacy", "common"}:
        return configured
    if configured != "auto":
        raise ClientError("INVALID_CONFIGURATION", "TODAY_OA_DEVICE_REQUEST_MODE must be auto, legacy, or common")
    request = urllib.request.Request(f"{API_URL}/v1/company-assets/meta", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            metadata = json.loads(response.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ClientError("MODE_DISCOVERY_FAILED", f"Unable to determine the device request mode: {error}") from None
    mode = metadata.get("requestWriteMode") if isinstance(metadata, dict) else None
    if mode not in {"legacy", "common"}:
        raise ClientError("INVALID_RESPONSE", "OA returned an invalid device request mode")
    return str(mode)


def write_private(path: Path, payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        raise ClientError("LOGIN_REQUIRED", "Google Workspace login is required") from None


def require_oauth_config() -> None:
    if not GOOGLE_CLIENT_ID:
        raise ClientError("OAUTH_NOT_CONFIGURED", "The installed Skill is missing its Google OAuth public client configuration")


def auth_start() -> dict[str, Any]:
    require_oauth_config()
    payload = request_json(
        "https://oauth2.googleapis.com/device/code",
        data={"client_id": GOOGLE_CLIENT_ID, "scope": SCOPES},
    )
    verification_url = payload.get("verification_url") or payload.get("verification_uri")
    required = ("device_code", "user_code", "expires_in", "interval")
    if any(key not in payload for key in required):
        raise ClientError("INVALID_RESPONSE", "Google returned an incomplete device authorization response")
    if not isinstance(verification_url, str):
        raise ClientError("INVALID_RESPONSE", "Google did not return an authorization URL")
    write_private(DEVICE_FILE, {
        "device_code": payload["device_code"],
        "expires_at": int(time.time()) + int(payload["expires_in"]),
        "interval": int(payload["interval"]),
    })
    return {
        "status": "authorization_required",
        "verification_url": verification_url,
        "user_code": payload["user_code"],
        "expires_in": payload["expires_in"],
    }


def auth_finish() -> dict[str, Any]:
    require_oauth_config()
    device = read_json(DEVICE_FILE)
    if int(device.get("expires_at", 0)) <= int(time.time()):
        raise ClientError("AUTHORIZATION_EXPIRED", "Google authorization expired; start again")
    try:
        tokens = broker_json(
            "/v1/auth/google-device/token",
            {"deviceCode": device["device_code"]},
        )
    except ClientError as error:
        if error.code in {"authorization_pending", "slow_down"}:
            return {"status": "authorization_pending", "retry_after": int(device.get("interval", 5))}
        raise
    if tokens.get("status") in {"authorization_pending", "slow_down"}:
        return {"status": "authorization_pending", "retry_after": int(device.get("interval", 5))}
    if not isinstance(tokens.get("idToken"), str) or not isinstance(tokens.get("refreshToken"), str):
        raise ClientError("INVALID_RESPONSE", "Google did not return the required identity tokens")
    write_private(TOKEN_FILE, {
        "id_token": tokens["idToken"],
        "refresh_token": tokens["refreshToken"],
        "expires_at": int(time.time()) + int(tokens.get("expiresIn", 3600)),
    })
    DEVICE_FILE.unlink(missing_ok=True)
    return {"status": "authenticated"}


def refresh_tokens(tokens: dict[str, Any]) -> dict[str, Any]:
    require_oauth_config()
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str):
        raise ClientError("LOGIN_REQUIRED", "Google Workspace login is required")
    refreshed = broker_json(
        "/v1/auth/google-device/refresh",
        {"refreshToken": refresh_token},
    )
    if not isinstance(refreshed.get("idToken"), str):
        raise ClientError("LOGIN_REQUIRED", "Google login must be renewed")
    updated = {
        "id_token": refreshed["idToken"],
        "refresh_token": refreshed.get("refreshToken", refresh_token),
        "expires_at": int(time.time()) + int(refreshed.get("expiresIn", 3600)),
    }
    write_private(TOKEN_FILE, updated)
    return updated


def active_id_token() -> str:
    tokens = read_json(TOKEN_FILE)
    if int(tokens.get("expires_at", 0)) <= int(time.time()) + 60:
        tokens = refresh_tokens(tokens)
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise ClientError("LOGIN_REQUIRED", "Google Workspace login is required")
    return id_token


def read_update_manifest(*, force: bool = False) -> dict[str, Any]:
    now = int(time.time())
    if not force:
        try:
            cached = read_json(UPDATE_CACHE_FILE)
            if now - int(cached.get("checkedAt", 0)) < UPDATE_CHECK_TTL_SECONDS:
                manifest = cached.get("manifest")
                if isinstance(manifest, dict):
                    return manifest
        except ClientError:
            pass

    request = urllib.request.Request(
        UPDATE_MANIFEST_URL,
        headers={"Accept": "application/json", "User-Agent": "Today-OA-Skill-Updater"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            manifest = json.loads(response.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ClientError("UPDATE_CHECK_FAILED", f"Unable to check for a Today OA update: {error}") from None
    if not isinstance(manifest, dict):
        raise ClientError("INVALID_UPDATE_MANIFEST", "Today OA returned an invalid update manifest")
    required = {"schemaVersion", "name", "version", "downloadUrl", "sha256"}
    if not required.issubset(manifest) or manifest.get("name") != "today-oa":
        raise ClientError("INVALID_UPDATE_MANIFEST", "Today OA returned an incomplete update manifest")
    write_private(UPDATE_CACHE_FILE, {"checkedAt": now, "manifest": manifest})
    return manifest


def start_background_update() -> bool:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        lock_fd = os.open(UPDATE_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(lock_fd)
    try:
        with UPDATE_LOG_FILE.open("ab") as log:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "update", "--wait", "--background-worker"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError:
        UPDATE_LOCK_FILE.unlink(missing_ok=True)
        return False
    return True


def update_status(*, force: bool = False, auto_update: bool = True) -> dict[str, Any]:
    current = str(LOCAL_RELEASE["version"])
    try:
        manifest = read_update_manifest(force=force)
    except ClientError as error:
        return {
            "status": "unknown",
            "currentVersion": current,
            "error": error.code,
            "message": str(error),
        }
    latest = str(manifest["version"])
    status = "up_to_date" if latest == current else "update_available"
    if status == "update_available" and auto_update:
        status = "update_started" if start_background_update() else "update_in_progress"
    return {
        "status": status,
        "currentVersion": current,
        "latestVersion": latest,
    }


def validate_release_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    expected_prefix = "/Today-Operation/today-oa-skills/releases/download/"
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(expected_prefix):
        raise ClientError("UNTRUSTED_UPDATE_SOURCE", "The update is not from the official Today OA release location")


def safe_extract_release(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError:
                raise ClientError("INVALID_UPDATE_PACKAGE", "The update package contains an unsafe path") from None
        package.extractall(destination)
    candidate = destination / "today-oa"
    required = [
        candidate / "SKILL.md",
        candidate / "scripts" / "today_oa.py",
        candidate / "references" / "release.json",
        candidate / "references" / "oauth-client.json",
    ]
    if not all(path.is_file() for path in required):
        raise ClientError("INVALID_UPDATE_PACKAGE", "The update package is incomplete")
    return candidate


def apply_update() -> dict[str, Any]:
    manifest = read_update_manifest(force=True)
    current = str(LOCAL_RELEASE["version"])
    latest = str(manifest["version"])
    if latest == current:
        return {"status": "up_to_date", "version": current}

    download_url = str(manifest["downloadUrl"])
    validate_release_url(download_url)
    with tempfile.TemporaryDirectory(prefix="today-oa-update-", dir=str(SKILL_ROOT.parent)) as temporary_dir:
        temporary = Path(temporary_dir)
        archive = temporary / "release.zip"
        try:
            request = urllib.request.Request(download_url, headers={"User-Agent": "Today-OA-Skill-Updater"})
            with urllib.request.urlopen(request, timeout=20) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as error:
            raise ClientError("UPDATE_DOWNLOAD_FAILED", f"Unable to download the Today OA update: {error}") from None
        actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        if not secrets.compare_digest(actual_hash, str(manifest["sha256"])):
            raise ClientError("UPDATE_INTEGRITY_FAILED", "The Today OA update package failed integrity verification")
        candidate = safe_extract_release(archive, temporary / "extracted")
        candidate_release = json.loads((candidate / "references" / "release.json").read_text(encoding="utf-8"))
        if candidate_release.get("name") != "today-oa" or str(candidate_release.get("version")) != latest:
            raise ClientError("INVALID_UPDATE_PACKAGE", "The update package version does not match its manifest")

        backup = SKILL_ROOT.parent / ".today-oa.rollback"
        if backup.exists():
            shutil.rmtree(backup)
        SKILL_ROOT.replace(backup)
        try:
            candidate.replace(SKILL_ROOT)
        except OSError:
            backup.replace(SKILL_ROOT)
            raise ClientError("UPDATE_INSTALL_FAILED", "Unable to activate the Today OA update") from None
        shutil.rmtree(backup, ignore_errors=True)
    return {"status": "updated", "previousVersion": current, "version": latest}


def confirmation_summary(action_input: dict[str, Any]) -> dict[str, Any]:
    action = action_input["action"]
    fields = {
        "create_extra_request": ("purpose", "expectedDate", "items"),
        "create_and_submit_extra_request": ("purpose", "expectedDate", "items"),
        "update_request": ("requestId", "purpose", "expectedDate", "items"),
        "resubmit_request": ("requestId", "purpose", "expectedDate", "items"),
        "submit_request": ("requestId",),
        "withdraw_request": ("requestId", "comment"),
        "request_return": ("assetId",),
        "approve_request": ("taskId",),
        "reject_request": ("taskId", "comment"),
        "request_changes": ("taskId", "comment"),
    }.get(action, ())
    return {field: action_input[field] for field in fields if field in action_input}

def legacy_execute(action_input: dict[str, Any], confirmed: bool, confirmation_token: str | None) -> dict[str, Any]:
    body = json.dumps({**action_input, "confirmed": confirmed}, ensure_ascii=False).encode()
    headers = {
        "authorization": f"Bearer {active_id_token()}",
        "content-type": "application/json",
        "x-oa-source": "skill",
    }
    if confirmed and confirmation_token:
        headers["idempotency-key"] = f"skill:{confirmation_token}"
    request = urllib.request.Request(
        f"{API_URL}/v1/company-assets/integrations/today/company-asset",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode())
            detail = payload.get("error", payload) if isinstance(payload, dict) else {}
            raise ClientError(str(detail.get("code", "HTTP_ERROR")), str(detail.get("message", "OA request failed")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ClientError("HTTP_ERROR", "OA request failed") from None
    except urllib.error.URLError as error:
        raise ClientError("NETWORK_ERROR", f"Unable to reach OA: {error.reason}") from None
    if not isinstance(result, dict):
        raise ClientError("INVALID_RESPONSE", "OA returned an invalid response")
    return result


def common_request_data(action_input: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = current.get("data", {}) if isinstance(current, dict) else {}
    data = dict(existing) if isinstance(existing, dict) else {}
    for source, target in (("purpose", "purpose"), ("expectedDate", "expectedDate"), ("items", "items")):
        if source in action_input:
            data[target] = action_input[source]
    return data


def common_execute(action_input: dict[str, Any], confirmation_token: str | None) -> dict[str, Any]:
    action = action_input["action"]
    limit = int(action_input.get("limit", 50))
    offset = int(action_input.get("offset", 0))
    if action == "list_my_requests":
        current = api_json(f"/v1/oa/applications?limit={limit}&offset={offset}")
        legacy = legacy_execute(action_input, False, None)
        return {
            "items": current.get("items", []),
            "legacyItems": legacy.get("items", []),
            "pagination": current.get("pagination", {"limit": limit, "offset": offset}),
            "historyCompatibility": "legacyItems are read-only historical device requests",
        }
    if action == "get_request":
        request_id = str(action_input.get("requestId", ""))
        try:
            result = api_json(f"/v1/oa/applications/{urllib.parse.quote(request_id)}")
            result["history"] = api_json(f"/v1/oa/applications/{urllib.parse.quote(request_id)}/history").get("items", [])
            return result
        except ClientError as error:
            if error.code != "NOT_FOUND":
                raise
            return legacy_execute(action_input, False, None)
    if action == "list_my_approvals":
        return api_json(f"/v1/oa/approvals/pending?limit={limit}&offset={offset}")

    key = f"skill:{confirmation_token}"
    request_id = str(action_input.get("requestId", ""))
    if action in {"create_extra_request", "create_and_submit_extra_request"}:
        created = api_json(
            f"/v1/oa/applications/{ASSET_REQUEST_BUSINESS_TYPE}",
            method="POST",
            payload={
                "data": common_request_data(action_input),
                "summary": str(action_input.get("purpose", "")),
            },
            idempotency_key=f"{key}:create",
        )
        if action == "create_extra_request":
            return created
        application_id = str(created.get("id", ""))
        if not application_id:
            raise ClientError("INVALID_RESPONSE", "OA did not return an application id")
        return api_json(
            f"/v1/oa/applications/{urllib.parse.quote(application_id)}/submit",
            method="POST",
            idempotency_key=f"{key}:submit",
        )
    if action in {"update_request", "resubmit_request"}:
        current = api_json(f"/v1/oa/applications/{urllib.parse.quote(request_id)}")
        payload = {
            "data": common_request_data(action_input, current),
            "summary": str(action_input.get("purpose", current.get("summary", ""))),
        }
        endpoint = "resubmit" if action == "resubmit_request" else "draft"
        method = "POST" if action == "resubmit_request" else "PUT"
        return api_json(
            f"/v1/oa/applications/{urllib.parse.quote(request_id)}/{endpoint}",
            method=method,
            payload=payload,
            idempotency_key=key,
        )
    if action == "submit_request":
        return api_json(
            f"/v1/oa/applications/{urllib.parse.quote(request_id)}/submit",
            method="POST",
            idempotency_key=key,
        )
    if action == "withdraw_request":
        payload = {"reason": action_input["comment"]} if action_input.get("comment") else {}
        return api_json(
            f"/v1/oa/applications/{urllib.parse.quote(request_id)}/withdraw",
            method="POST",
            payload=payload,
            idempotency_key=key,
        )
    if action in {"approve_request", "reject_request", "request_changes"}:
        approval_action = {
            "approve_request": "approve",
            "reject_request": "reject",
            "request_changes": "request_changes",
        }[action]
        payload = {"action": approval_action}
        if action_input.get("comment"):
            payload["reason"] = action_input["comment"]
        return api_json(
            f"/v1/oa/approvals/{urllib.parse.quote(str(action_input.get('taskId', '')))}/actions",
            method="POST",
            payload=payload,
            idempotency_key=key,
        )
    raise ClientError("INVALID_INPUT", f"Action {action} is not supported in common request mode")


def execute(action_input: dict[str, Any]) -> dict[str, Any]:
    action_input = dict(action_input)
    action = action_input.get("action")
    if not isinstance(action, str):
        raise ClientError("INVALID_INPUT", "action is required")
    is_write = action in WRITE_ACTIONS
    confirmed = action_input.get("confirmed") is True
    confirmation_token = action_input.pop("confirmationToken", None)
    if is_write and confirmed and not isinstance(confirmation_token, str):
        raise ClientError("CONFIRMATION_REQUIRED", "confirmationToken is required for a confirmed write")
    if is_write and confirmed:
        validate_confirmation(confirmation_token, action_input)
    if action in CONTRACT_ACTIONS:
        if is_write and not confirmed:
            result = {
                "status": "confirmation_required",
                "summary": contract_confirmation_summary(action, action_input),
            }
        else:
            result = execute_contract_action(action, action_input, confirmation_token)
        if is_write and not confirmed:
            result["confirmationToken"] = secrets.token_hex(16)
            record_confirmation(result["confirmationToken"], action_input)
        if is_write and confirmed:
            consume_confirmation(confirmation_token)
        return result
    mode = device_request_mode() if action in COMMON_APPLICATION_ACTIONS else "legacy"
    if mode == "common" and is_write and not confirmed:
        result = {
            "status": "confirmation_required",
            "action": action,
            "summary": confirmation_summary(action_input),
            "requestWriteMode": "common",
        }
    elif mode == "common":
        result = common_execute(action_input, confirmation_token)
    else:
        if action == "resubmit_request":
            raise ClientError("ACTION_NOT_AVAILABLE", "Resubmit is available after OA switches device requests to common mode")
        result = legacy_execute(action_input, confirmed, confirmation_token)
    if is_write and not confirmed and result.get("status") == "confirmation_required":
        result["confirmationToken"] = secrets.token_hex(16)
        record_confirmation(result["confirmationToken"], action_input)
    if is_write and confirmed:
        consume_confirmation(confirmation_token)
    return result


def required_string(action_input: dict[str, Any], field: str) -> str:
    value = action_input.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ClientError("INVALID_INPUT", f"{field} is required")
    return value.strip()


def positive_page_value(action_input: dict[str, Any], field: str, default: int, maximum: int) -> int:
    value = action_input.get(field, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > maximum:
        raise ClientError("INVALID_INPUT", f"{field} must be an integer between 0 and {maximum}")
    return value


def contract_confirmation_summary(action: str, action_input: dict[str, Any]) -> dict[str, Any]:
    summaries = {
        "contract_create_draft": "新建合同草稿",
        "contract_update_draft": "更新合同草稿",
        "contract_add_attachment": "登记合同附件新版本",
        "contract_submit": "提交合同审批",
        "contract_withdraw": "撤回并取消合同申请",
        "contract_approve": "通过合同审批",
        "contract_reject": "驳回并终止合同审批",
        "contract_delegate": "将合同审批转签给他人",
    }
    summary: dict[str, Any] = {"operation": summaries[action]}
    for field in ("applicationId", "taskId", "reason"):
        if field in action_input:
            summary[field] = action_input[field]
    if action == "contract_create_draft" or action == "contract_update_draft":
        data = action_input.get("data")
        if isinstance(data, dict):
            summary.update({
                "title": data.get("title"),
                "contractType": data.get("contract_type"),
                "counterparty": data.get("counterparty"),
                "contractValue": data.get("contract_value"),
            })
    if action == "contract_add_attachment":
        attachment = action_input.get("attachment")
        if isinstance(attachment, dict):
            summary["attachment"] = {
                "fileName": attachment.get("fileName"),
                "category": attachment.get("category"),
                "storageProvider": attachment.get("storageProvider"),
            }
    if action == "contract_delegate":
        summary["targetEmployeeId"] = action_input.get("targetEmployeeId")
    return summary


def execute_contract_action(action: str, action_input: dict[str, Any], confirmation_token: str | None) -> dict[str, Any]:
    application_id = urllib.parse.quote(str(action_input.get("applicationId", "")), safe="")
    task_id = urllib.parse.quote(str(action_input.get("taskId", "")), safe="")
    method = "GET"
    path = ""
    body: dict[str, Any] | None = None

    if action == "contract_list_legal_entities":
        path = "/v1/contracts/legal-entities"
    elif action == "contract_list_my_applications":
        limit = positive_page_value(action_input, "limit", 20, 100)
        offset = positive_page_value(action_input, "offset", 0, 1_000_000)
        path = f"/v1/contracts/applications?{urllib.parse.urlencode({'limit': limit, 'offset': offset})}"
    elif action == "contract_list_pending_approvals":
        limit = positive_page_value(action_input, "limit", 20, 100)
        offset = positive_page_value(action_input, "offset", 0, 1_000_000)
        path = f"/v1/contracts/approvals/pending?{urllib.parse.urlencode({'limit': limit, 'offset': offset})}"
    elif action == "contract_get_application":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        path = f"/v1/contracts/applications/{application_id}"
    elif action == "contract_get_history":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        path = f"/v1/contracts/applications/{application_id}/history"
    elif action == "contract_create_draft":
        method, path = "POST", "/v1/contracts/drafts"
        body = contract_draft_body(action_input)
    elif action == "contract_update_draft":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        method, path = "PUT", f"/v1/contracts/applications/{application_id}/draft"
        body = contract_draft_body(action_input)
    elif action == "contract_add_attachment":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        attachment = action_input.get("attachment")
        if not isinstance(attachment, dict):
            raise ClientError("INVALID_INPUT", "attachment is required")
        method, path, body = "POST", f"/v1/contracts/applications/{application_id}/attachments", attachment
    elif action == "contract_submit":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        method, path, body = "POST", f"/v1/contracts/applications/{application_id}/submit", {}
    elif action == "contract_withdraw":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        method, path = "POST", f"/v1/contracts/applications/{application_id}/withdraw"
        body = {"reason": action_input["reason"]} if action_input.get("reason") else {}
    elif action in {"contract_approve", "contract_reject"}:
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        task_id = urllib.parse.quote(required_string(action_input, "taskId"), safe="")
        decision = "approve" if action == "contract_approve" else "reject"
        body = {"action": decision}
        if action_input.get("reason"):
            body["reason"] = action_input["reason"]
        if decision == "reject" and not action_input.get("reason"):
            raise ClientError("INVALID_INPUT", "reason is required for contract rejection")
        method, path = "POST", f"/v1/contracts/applications/{application_id}/approvals/{task_id}/actions"
    elif action == "contract_delegate":
        application_id = urllib.parse.quote(required_string(action_input, "applicationId"), safe="")
        task_id = urllib.parse.quote(required_string(action_input, "taskId"), safe="")
        method, path = "POST", f"/v1/contracts/applications/{application_id}/approvals/{task_id}/assignment"
        body = {
            "action": "delegate",
            "targetEmployeeId": required_string(action_input, "targetEmployeeId"),
            "reason": required_string(action_input, "reason"),
        }
    else:
        raise ClientError("INVALID_ACTION", "Unsupported contract action")

    headers = {
        "authorization": f"Bearer {active_id_token()}",
        "accept": "application/json",
        "x-oa-source": "skill",
    }
    encoded = None
    if body is not None:
        headers["content-type"] = "application/json"
        encoded = json.dumps(body, ensure_ascii=False).encode()
    if confirmation_token:
        headers["idempotency-key"] = f"skill:{confirmation_token}"
    request = urllib.request.Request(f"{API_URL}{path}", data=encoded, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode())
            detail = payload.get("error", payload) if isinstance(payload, dict) else {}
            if isinstance(detail, dict):
                raise ClientError(str(detail.get("code", "HTTP_ERROR")), str(detail.get("message", "OA request failed")))
            raise ClientError("HTTP_ERROR", str(detail))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ClientError("HTTP_ERROR", "OA request failed") from None
    except urllib.error.URLError as error:
        raise ClientError("NETWORK_ERROR", f"Unable to reach OA: {error.reason}") from None
    if not isinstance(result, dict):
        raise ClientError("INVALID_RESPONSE", "OA returned an invalid response")
    return result


def contract_draft_body(action_input: dict[str, Any]) -> dict[str, Any]:
    data = action_input.get("data")
    if not isinstance(data, dict):
        raise ClientError("INVALID_INPUT", "data is required")
    forbidden = {"handler_employee_id", "business_owner_employee_id"}.intersection(data)
    if forbidden:
        raise ClientError("IDENTITY_FIELD_FORBIDDEN", "Employee identity fields are filled by OA from the verified login")
    body: dict[str, Any] = {"data": data}
    if "summary" in action_input:
        body["summary"] = action_input["summary"]
    if "deepLink" in action_input:
        body["deepLink"] = action_input["deepLink"]
    return body


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Today OA workflow client")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("auth-start")
    subparsers.add_parser("auth-finish")
    subparsers.add_parser("auth-status")
    subparsers.add_parser("logout")
    update_status_parser = subparsers.add_parser("update-status")
    update_status_parser.add_argument("--force", action="store_true")
    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--wait", action="store_true", help="wait for the verified update to finish")
    update_parser.add_argument("--background-worker", action="store_true", help=argparse.SUPPRESS)
    subparsers.add_parser("version")
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--input-json", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        if args.command == "auth-start":
            result = auth_start()
        elif args.command == "auth-finish":
            result = auth_finish()
        elif args.command == "auth-status":
            try:
                active_id_token()
                result = {"status": "authenticated"}
            except ClientError as error:
                if error.code == "OAUTH_NOT_CONFIGURED":
                    result = {"status": "setup_required"}
                elif error.code == "LOGIN_REQUIRED":
                    result = {"status": "login_required"}
                else:
                    raise
        elif args.command == "logout":
            TOKEN_FILE.unlink(missing_ok=True)
            DEVICE_FILE.unlink(missing_ok=True)
            CONFIRMATION_FILE.unlink(missing_ok=True)
            result = {"status": "logged_out"}
        elif args.command == "update-status":
            result = update_status(force=args.force)
        elif args.command == "update":
            if not args.wait:
                raise ClientError("EXPLICIT_WAIT_REQUIRED", "Run update with --wait so completion can be verified")
            try:
                result = apply_update()
            finally:
                if args.background_worker:
                    UPDATE_LOCK_FILE.unlink(missing_ok=True)
        elif args.command == "version":
            result = {"name": "today-oa", "version": str(LOCAL_RELEASE["version"])}
        else:
            parsed = json.loads(args.input_json)
            if not isinstance(parsed, dict):
                raise ClientError("INVALID_INPUT", "input JSON must be an object")
            result = execute(parsed)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except json.JSONDecodeError:
        error = ClientError("INVALID_INPUT", "input JSON is invalid")
    except ClientError as caught:
        error = caught
    print(json.dumps({"ok": False, "error": error.code, "message": str(error)}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
