import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "today-oa/scripts/today_oa.py"
SPEC = importlib.util.spec_from_file_location("today_oa_skill", SCRIPT)
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TodayOASkillClientTest(unittest.TestCase):
    def test_contract_test_branch_targets_the_isolated_oa_environment(self):
        self.assertEqual(client.LOCAL_RELEASE["channel"], "test")
        self.assertEqual(
            client.API_URL,
            "https://oa-platform-api-test-3byd72bk3q-as.a.run.app",
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.originals = (
            client.STATE_DIR,
            client.TOKEN_FILE,
            client.DEVICE_FILE,
            client.CONFIRMATION_FILE,
            client.UPDATE_CACHE_FILE,
            client.UPDATE_LOCK_FILE,
            client.UPDATE_LOG_FILE,
        )
        client.STATE_DIR = directory
        client.TOKEN_FILE = directory / "tokens.json"
        client.DEVICE_FILE = directory / "device.json"
        client.CONFIRMATION_FILE = directory / "confirmations.json"
        client.UPDATE_CACHE_FILE = directory / "update-check.json"
        client.UPDATE_LOCK_FILE = directory / "update.lock"
        client.UPDATE_LOG_FILE = directory / "update.log"

    def tearDown(self):
        (
            client.STATE_DIR,
            client.TOKEN_FILE,
            client.DEVICE_FILE,
            client.CONFIRMATION_FILE,
            client.UPDATE_CACHE_FILE,
            client.UPDATE_LOCK_FILE,
            client.UPDATE_LOG_FILE,
        ) = self.originals
        self.temporary.cleanup()

    def test_auth_start_saves_only_device_state(self):
        response = {
            "device_code": "private-device-code",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://google.com/device",
            "expires_in": 1800,
            "interval": 5,
        }
        with patch.object(client, "GOOGLE_CLIENT_ID", "client-id"), patch.object(
            client, "request_json", return_value=response
        ):
            result = client.auth_start()

        self.assertEqual(result["status"], "authorization_required")
        self.assertNotIn("device_code", result)
        self.assertEqual(client.DEVICE_FILE.stat().st_mode & 0o777, 0o600)

    def test_auth_finish_exchanges_device_code_through_oa_broker(self):
        client.write_private(client.DEVICE_FILE, {
            "device_code": "private-device-code",
            "expires_at": 9999999999,
            "interval": 5,
        })
        with patch.object(client, "GOOGLE_CLIENT_ID", "client-id"), patch.object(
            client,
            "broker_json",
            return_value={
                "status": "authenticated",
                "idToken": "id-token",
                "refreshToken": "refresh-token",
                "expiresIn": 3600,
            },
        ) as broker:
            result = client.auth_finish()

        self.assertEqual(result, {"status": "authenticated"})
        broker.assert_called_once_with(
            "/v1/auth/google-device/token",
            {"deviceCode": "private-device-code"},
        )

    def test_preview_binds_confirmation_to_exact_action(self):
        action = {"action": "request_return", "assetId": "asset-1", "confirmed": False}
        with patch.object(client, "active_id_token", return_value="id-token"), patch.object(
            client.urllib.request,
            "urlopen",
            return_value=FakeResponse({"status": "confirmation_required", "summary": {"assetId": "asset-1"}}),
        ), patch.object(client.secrets, "token_hex", return_value="confirmation-1"):
            result = client.execute(action)

        self.assertEqual(result["confirmationToken"], "confirmation-1")
        client.validate_confirmation("confirmation-1", action)
        with self.assertRaises(client.ClientError) as error:
            client.validate_confirmation(
                "confirmation-1",
                {"action": "request_return", "assetId": "asset-2", "confirmed": True},
            )
        self.assertEqual(error.exception.code, "CONFIRMATION_MISMATCH")

    def test_confirmed_write_uses_replay_safe_key(self):
        captured = {}
        action = {
            "action": "submit_request",
            "requestId": "request-1",
            "expectedVersion": 2,
            "confirmed": True,
        }
        client.record_confirmation("confirmation-1", action)

        def open_request(request, timeout):
            captured["idempotency"] = request.headers["Idempotency-key"]
            captured["body"] = json.loads(request.data.decode())
            self.assertEqual(timeout, 20)
            return FakeResponse({"status": "submitted"})

        with patch.object(client, "active_id_token", return_value="id-token"), patch.object(
            client.urllib.request, "urlopen", side_effect=open_request
        ):
            result = client.execute({**action, "confirmationToken": "confirmation-1"})

        self.assertEqual(result, {"status": "submitted"})
        self.assertEqual(captured["idempotency"], "skill:confirmation-1")
        self.assertNotIn("confirmationToken", captured["body"])

    def test_contract_write_preview_is_local_and_binds_exact_payload(self):
        action = {
            "action": "contract_submit",
            "applicationId": "application-1",
            "confirmed": False,
        }
        with patch.object(client.urllib.request, "urlopen") as urlopen, patch.object(
            client.secrets, "token_hex", return_value="contract-confirmation-1"
        ):
            result = client.execute(action)

        urlopen.assert_not_called()
        self.assertEqual(result["status"], "confirmation_required")
        self.assertEqual(result["summary"]["operation"], "提交合同审批")
        client.validate_confirmation("contract-confirmation-1", action)

    def test_confirmed_contract_create_uses_direct_contract_api_and_verified_identity(self):
        captured = {}
        action = {
            "action": "contract_create_draft",
            "summary": "软件合同",
            "data": {"version": 1, "contract_type": "software_purchase"},
            "confirmed": True,
        }
        client.record_confirmation("contract-confirmation-1", action)

        def open_request(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["headers"] = request.headers
            captured["body"] = json.loads(request.data.decode())
            self.assertEqual(timeout, 20)
            return FakeResponse({"id": "application-1", "status": "draft"})

        with patch.object(client, "active_id_token", return_value="google-id-token"), patch.object(
            client.urllib.request, "urlopen", side_effect=open_request
        ):
            result = client.execute({**action, "confirmationToken": "contract-confirmation-1"})

        self.assertEqual(result["status"], "draft")
        self.assertTrue(captured["url"].endswith("/v1/contracts/drafts"))
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer google-id-token")
        self.assertEqual(captured["headers"]["Idempotency-key"], "skill:contract-confirmation-1")
        self.assertEqual(captured["body"], {
            "summary": "软件合同",
            "data": {"version": 1, "contract_type": "software_purchase"},
        })

    def test_contract_query_uses_pagination_and_no_idempotency_key(self):
        captured = {}

        def open_request(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["headers"] = request.headers
            self.assertEqual(timeout, 20)
            return FakeResponse({"items": [], "pagination": {"limit": 10, "offset": 20}})

        with patch.object(client, "active_id_token", return_value="google-id-token"), patch.object(
            client.urllib.request, "urlopen", side_effect=open_request
        ):
            result = client.execute({"action": "contract_list_my_applications", "limit": 10, "offset": 20})

        self.assertEqual(result["items"], [])
        self.assertIn("/v1/contracts/applications?limit=10&offset=20", captured["url"])
        self.assertEqual(captured["method"], "GET")
        self.assertNotIn("Idempotency-key", captured["headers"])

    def test_contract_draft_rejects_conversation_identity_fields(self):
        with self.assertRaises(client.ClientError) as error:
            client.contract_draft_body({
                "data": {"version": 1, "handler_employee_id": "user-supplied"},
            })
        self.assertEqual(error.exception.code, "IDENTITY_FIELD_FORBIDDEN")

    def test_update_status_does_not_block_when_manifest_is_unavailable(self):
        with patch.object(
            client,
            "read_update_manifest",
            side_effect=client.ClientError("UPDATE_CHECK_FAILED", "offline"),
        ):
            result = client.update_status(auto_update=False)

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["currentVersion"], "0.1.0")

    def test_update_status_reports_new_release(self):
        with patch.object(
            client,
            "read_update_manifest",
            return_value={"version": "0.2.0"},
        ):
            result = client.update_status(auto_update=False)

        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["latestVersion"], "0.2.0")

    def test_update_status_starts_verified_update_in_background(self):
        with patch.object(
            client,
            "read_update_manifest",
            return_value={"version": "0.2.0"},
        ), patch.object(client, "start_background_update", return_value=True) as start:
            result = client.update_status()

        self.assertEqual(result["status"], "update_started")
        start.assert_called_once_with()

    def test_background_update_uses_lock_to_avoid_duplicate_workers(self):
        with patch.object(client.subprocess, "Popen") as process:
            self.assertTrue(client.start_background_update())
            self.assertFalse(client.start_background_update())

        process.assert_called_once()

    def test_today_install_keeps_state_outside_replaceable_skill_directory(self):
        state = client.default_state_dir(
            Path("/home/user/.today/skills/community/today-oa")
        )
        self.assertEqual(state, Path("/home/user/.today/skills/community/.state/today-oa"))


if __name__ == "__main__":
    unittest.main()
