# Contract approval actions

All commands use `python3 scripts/today_oa.py execute --input-json '<JSON object>'`.

Identity always comes from the verified Google Workspace login. Never add `handler_employee_id`, an email, tenant, role, or token to an action. OA fills the handler from the authenticated principal.

## Contract types and required submission data

- `brand`: 品牌合同
- `marketing`: 市场营销合同
- `software_purchase`: 软件采购合同
- `headhunter`: 猎头合同
- `other`: 其他合同

Before submission the contract needs: `version` (`1`), `contract_type`, `core_terms_summary`, `counterparty.name`, `legal_entity_id`, `has_contract_value`, `contract_value`, `approval_value_cny_minor`, `fx_rate_snapshot_id`, and at least one `contract_draft` attachment.

Use `template_mode: "custom"` unless OA returns an enabled standard template. For CNY, amount fields are integer fen and `approval_value_cny_minor` equals `contract_value.amount_minor`. For a no-amount contract, use `contract_value: null`, `approval_value_cny_minor: null`, and `fx_rate_snapshot_id: null`. For non-CNY, query the published quarterly FX table through a supported OA surface; never invent a snapshot or conversion.

## Queries

- Company legal entities: `{"action":"contract_list_legal_entities"}`
- My applications: `{"action":"contract_list_my_applications","limit":20,"offset":0}`
- Application detail including attachment versions: `{"action":"contract_get_application","applicationId":"<uuid>"}`
- Application history: `{"action":"contract_get_history","applicationId":"<uuid>"}`
- My pending approvals: `{"action":"contract_list_pending_approvals","limit":20,"offset":0}`

## Applicant writes

- Save draft: `{"action":"contract_create_draft","summary":"年度软件服务合同","data":{"version":1,"contract_type":"software_purchase","template_mode":"custom","title":"年度软件服务合同","core_terms_summary":"采购一年期软件订阅，验收后付款","counterparty":{"name":"示例供应商"},"legal_entity_id":"<from contract_list_legal_entities>","has_contract_value":true,"contract_value":{"amount_minor":5000000,"currency":"CNY"},"approval_value_cny_minor":5000000,"fx_rate_snapshot_id":null,"auto_renewal":false,"payment_schedule":[]},"confirmed":false}`
- Update draft: fetch the latest detail, merge the user's changes into the complete `data`, then call `{"action":"contract_update_draft","applicationId":"<uuid>","summary":"...","data":{...},"confirmed":false}`. A draft update replaces the draft data; do not send only the changed field.
- Register an attachment version: `{"action":"contract_add_attachment","applicationId":"<uuid>","attachment":{"category":"contract_draft","storageProvider":"google_drive","storageObjectId":"<verified object id>","fileName":"合同文件-v1.pdf","contentType":"application/pdf","sizeBytes":1234,"sha256":"<64 lowercase hex>"},"confirmed":false}`
- Submit: fetch detail first, verify required data and a `contract_draft` attachment, then call `{"action":"contract_submit","applicationId":"<uuid>","confirmed":false}`.
- Withdraw: allowed before final approval and cancels the original application: `{"action":"contract_withdraw","applicationId":"<uuid>","reason":"不再继续签署","confirmed":false}`

The attachment action only registers an already stored object. In an isolated test deployment, `storageProvider: "mock"` may be used only when the user is explicitly testing and the test environment is configured for simulated storage. Describe it as a simulated attachment, not a Google Drive upload.

## Approval writes

- Approve: `{"action":"contract_approve","applicationId":"<uuid>","taskId":"<uuid>","confirmed":false}`
- Reject and terminate: `{"action":"contract_reject","applicationId":"<uuid>","taskId":"<uuid>","reason":"驳回原因","confirmed":false}`
- Delegate: `{"action":"contract_delegate","applicationId":"<uuid>","taskId":"<uuid>","targetEmployeeId":"<uuid returned by an authorized OA directory>","reason":"转签原因","confirmed":false}`

Never ask the user to type an internal employee ID. If no authorized OA result supplied the target employee ID, explain that name-based delegation is not yet available in the installed version.

## Confirmation and status behavior

Every write first returns a preview and `confirmationToken`. Present it in plain Chinese and wait for explicit confirmation. Then resend the exact action with `confirmed: true` and the token.

- `draft`: 草稿
- `in_review`: 审批中
- `approved`: 已批准
- `rejected`: 已驳回并终止
- `withdrawn`: 已撤回并取消
- `cancelled`: 管理员已取消

Do not describe mock Slack, mock archive, or mock signing results as real external integrations.
