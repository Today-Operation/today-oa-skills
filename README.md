# Today OA Skills

Official distributable Skills for Today internal workflows.

Employees install the single `today-oa` Skill once. Device management, contract approval, and expense reimbursement share the same verified identity and confirmation flow; procurement and other OA modules will be added to the same Skill over time.

Install from Today by asking:

> 请安装 Today OA Skill：https://github.com/Today-Operation/today-oa-skills/tree/main/today-oa

The compatibility test branch keeps the production defaults unchanged. Test runs must provide an isolated API base, OAuth client ID, and state directory through `TODAY_OA_API_URL`, `TODAY_OA_GOOGLE_CLIENT_ID`, and `TODAY_OA_STATE_DIR`:

> 请安装合同与设备兼容测试版 Today OA Skill：https://github.com/Today-Operation/today-oa-skills/tree/agent/contracts-device-compat-20260818/today-oa

The unified test candidate adds expense reimbursement without changing the stable channel. It must use the isolated test API, OAuth client, and state directory; users should not install it until the test release is published and the acceptance checklist says it is ready.

The Skill uses verified Google Workspace identity and calls the Today OA API directly. It does not contain Slack, banking, OA service, or employee credentials.

Device request writes follow the server-advertised single `legacy` or `common`
mode. Common mode uses the public OA application and approval API while keeping
legacy requests read-only; the client never dual-writes the two approval paths.
