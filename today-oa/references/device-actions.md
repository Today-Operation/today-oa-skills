# Device management actions

All commands use `python3 scripts/today_oa.py execute --input-json '<JSON object>'`.

## Queries

- Catalog: `{"action":"list_catalog_items"}`
- My assets: `{"action":"list_my_assets"}`
- My requests: `{"action":"list_my_requests","limit":50,"offset":0}`
- Request detail: `{"action":"get_request","requestId":"<uuid>"}`
- My approvals: `{"action":"list_my_approvals","limit":50,"offset":0}`

The client discovers the server's single `legacy` or `common` request mode from
`/v1/company-assets/meta`. In `common` mode, current requests and approvals use
the public `/v1/oa` API. `list_my_requests` also returns old device requests in
the separate `legacyItems` read-only field, and `get_request` falls back to the
old read-only record when the public application is not found.

## Request writes

- Create and submit: `{"action":"create_and_submit_extra_request","purpose":"开发使用","expectedDate":"2026-08-20","items":[{"catalogItemId":"<uuid>","quantity":1}],"confirmed":false}`
- Save draft: use `create_extra_request` with the same fields.
- Update: `{"action":"update_request","requestId":"<uuid>","expectedVersion":2,"purpose":"更新后的用途","items":[...],"confirmed":false}`
- Revise and resubmit after changes are requested: `{"action":"resubmit_request","requestId":"<uuid>","purpose":"更新后的用途","items":[...],"confirmed":false}`
- Submit: `{"action":"submit_request","requestId":"<uuid>","expectedVersion":2,"confirmed":false}`
- Withdraw: `{"action":"withdraw_request","requestId":"<uuid>","expectedVersion":2,"confirmed":false}`
- Request return: `{"action":"request_return","assetId":"<uuid>","confirmed":false}`

Each catalog item is serialized; quantity must be `1` and the same catalog item must not repeat.

## Approval writes

- Approve: `{"action":"approve_request","taskId":"<uuid>","confirmed":false}`
- Reject: `{"action":"reject_request","taskId":"<uuid>","comment":"原因","confirmed":false}`
- Request changes: `{"action":"request_changes","taskId":"<uuid>","comment":"需要修改的内容","confirmed":false}`

`reject_request` and `request_changes` require a comment.

`resubmit_request` is available after the server switches device requests to
`common` mode. It creates one immutable public application revision and submits
that revision; it never writes the legacy device approval tables.

## Request-mode safety

- Do not set `TODAY_OA_DEVICE_REQUEST_MODE` for normal installs. Automatic mode
  discovery prevents a client release from writing both request systems.
- Operators may set it to exactly `legacy` or `common` only in an isolated test
  deployment.
- Catalog, assigned-device and return actions remain device-domain API calls in
  either mode. Application and approval writes use exactly one request system.

## Confirmation

A preview response includes `confirmationToken`. After explicit confirmation, resend the same payload with `confirmed: true` and that token. Keep every other action field unchanged.
