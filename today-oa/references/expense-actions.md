# Expense reimbursement actions

All commands use `python3 scripts/today_oa.py execute --input-json '<JSON object>'`.

Identity always comes from the verified Google Workspace login. Never add an employee ID, email, tenant, role, Google token, bank-account text, identity number, or shared service secret to an action.

## Queries

- My claims: `{"action":"expense_list_my_claims"}`
- Claim detail: `{"action":"expense_get_claim","claimId":"<uuid>"}`
- Claim history: `{"action":"expense_get_history","claimId":"<uuid>"}`
- My pending approvals: `{"action":"expense_list_pending_approvals"}`

## Applicant writes

- Create a draft: `{"action":"expense_create_draft","data":{"legalEntityId":"<authorized OA result>","reimbursementCurrency":"CNY","costCenterRef":"<authorized OA result>","beneficiaryRef":"<authorized OA result>","paymentAccountRef":"<authorized OA result>","purpose":"客户现场交通费","lines":[{"categoryCode":"travel","description":"高铁票","incurredOn":"2026-08-15","merchant":"中国铁路","amountMinor":128800,"taxAmountMinor":3752,"currency":"CNY","invoiceType":"electronic","invoiceNumber":"TEST-INV-001"}]},"confirmed":false}`
- Update or resubmit a returned draft: fetch the latest claim, merge the user's changes into the complete `data`, then call `{"action":"expense_update_draft","claimId":"<uuid>","data":{...},"confirmed":false}`. A save after `changes_requested` resubmits the same claim and preserves history.
- Register an uploaded attachment: `{"action":"expense_add_attachment","claimId":"<uuid>","attachment":{"storageProvider":"mock","storageObjectId":"<test object id>","fileName":"测试发票.pdf","contentType":"application/pdf","sizeBytes":1234,"sha256":"<64 lowercase hex>"},"confirmed":false}`
- Replace an attachment version: use `expense_replace_attachment` with `claimId`, `attachmentId`, and a new `attachment` object.
- Submit: `{"action":"expense_submit","claimId":"<uuid>","confirmed":false}`
- Withdraw: `{"action":"expense_withdraw","claimId":"<uuid>","reason":"不再报销","confirmed":false}`
- Cancel: `{"action":"expense_cancel","claimId":"<uuid>","reason":"重复申请","confirmed":false}`

The attachment action only registers an already stored object and SHA-256. Never claim the file was uploaded unless the returned storage provider proves it. `storageProvider: "mock"` is allowed only in an explicitly isolated test environment.

## Approval writes

- Approve: `{"action":"expense_approve","claimId":"<uuid>","confirmed":false}`
- Request changes: `{"action":"expense_request_changes","claimId":"<uuid>","reason":"请补充发票","confirmed":false}`
- Reject: `{"action":"expense_reject","claimId":"<uuid>","reason":"不符合报销政策","confirmed":false}`

Approval through Slack is the default manager experience. These actions are a Today fallback and still enforce server-side task ownership, identity, status, and idempotency.

## Scope boundary

Finance review, payment initiation/result, and accounting archive are administrator actions in Retool and are intentionally unavailable to ordinary employees through this Skill. Expense approval does not mean payment succeeded. The isolated test environment must never call a real bank.

Every write first returns a preview and `confirmationToken`. Present the preview in plain Chinese, wait for explicit confirmation, then resend the exact action with `confirmed: true` and the token.
