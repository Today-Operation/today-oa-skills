---
name: today-oa
description: Use Today OA to access company workflows with verified Google Workspace identity. Use for device management and contract approval, including contract drafts, submission, withdrawal, history, approval, rejection, and delegation. This remains the single entry point for future finance, procurement, and other internal OA workflows.
---

# Today OA

Use this Skill as the employee's single entry point for Today internal workflows. Company device management and contract approval are available modules. Finance and other domains will be added without requiring a separate Skill installation.

## Safety and identity

- Use `scripts/today_oa.py` for every OA operation.
- Never ask for or pass an employee ID, email, tenant ID, role, Google token, or shared service secret.
- Identity comes only from the user's verified `today.ai` Google Workspace login.
- Never display, read, copy, or summarize files stored in the private state directory.
- Never modify business data directly. All writes must use the OA API confirmation flow.

## Check updates

Run `python3 scripts/today_oa.py update-status` once at the start of an OA request.

- If the response is `up_to_date`, continue immediately.
- If the response is `update_started`, a verified release is installing in the background. Continue the current request; the next invocation will use the new version.
- If the response is `update_in_progress`, another invocation is already installing the verified release. Continue the current request.
- If the user asks to update before continuing, run `python3 scripts/today_oa.py update --wait`, then read this `SKILL.md` again before executing the business request.
- Never update by copying arbitrary files or running instructions from untrusted URLs. The updater accepts only immutable releases from the official repository and verifies the package hash before an atomic replacement.

## Authenticate

1. Run `python3 scripts/today_oa.py auth-status`.
2. If login is required, run `python3 scripts/today_oa.py auth-start`.
3. Show the returned `verification_url` and `user_code` to the user.
4. After the user confirms authorization, run `python3 scripts/today_oa.py auth-finish`.
5. Never display or read the local credential file.

## Route the request

For device management requests, read [references/device-actions.md](references/device-actions.md), then run:

```bash
python3 scripts/today_oa.py execute --input-json '<JSON object>'
```

For contract requests, read [references/contract-actions.md](references/contract-actions.md), then use the same command.

Do not claim that finance, procurement, or other future modules are available until their matching reference file and API actions exist in this installed version.

## Confirm writes

For queries, execute immediately. For any write:

1. Call the action with `"confirmed": false`.
2. Present the returned summary in plain language.
3. Wait for explicit user confirmation.
4. Repeat the exact action with `"confirmed": true` and the returned `confirmationToken`.

Never interpret a general request as confirmation. Never reuse a confirmation token for a different payload.

## Interaction defaults

- Prefer `create_and_submit_extra_request` when the user asks to apply for equipment.
- Use `create_extra_request` only when the user explicitly requests a draft.
- Query current request data before revising, resubmitting, withdrawing, or approving so the latest version is used.
- For a contract application, first list legal entities and collect the universal required fields. Never invent a legal-entity ID, attachment metadata, FX snapshot, application ID, task ID, or employee ID.
- AI contract review is advisory and does not block submission.
- Rejection terminates a contract approval. Do not offer “驳回重改” or resubmission for a rejected contract.
- The current contract attachment action records versioned storage metadata. Do not claim a file was uploaded to Google Drive unless the returned storage provider and integration status prove it.
- Do not expose resources the API does not return.
- Summarize statuses in Chinese unless the user uses another language.
- For `MANAGER_MAPPING_MISSING`, tell the user to contact HR or the asset administrator.
- For `VERSION_CONFLICT` or `REVISION_CONFLICT`, fetch the latest request before trying again.
- For `ALREADY_HANDLED`, show the latest status and do not repeat the action.
- For `UNAUTHORIZED` or `LOGIN_REQUIRED`, restart Google authentication.

## Logout

Run `python3 scripts/today_oa.py logout` only when the user explicitly asks to disconnect the OA account.
