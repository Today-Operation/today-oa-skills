# Today OA Skills

Official distributable Skills for Today internal workflows.

Employees install the single `today-oa` Skill once. Device management and contract approval share the same verified identity and confirmation flow; finance, procurement, and other OA modules will be added to the same Skill over time.

Install from Today by asking:

> 请安装 Today OA Skill：https://github.com/Today-Operation/today-oa-skills/tree/main/today-oa

The contract test branch is intentionally pinned to the isolated OA test API and does not change the stable release:

> 请安装合同测试版 Today OA Skill：https://github.com/Today-Operation/today-oa-skills/tree/agent/contracts-module-20260818/today-oa

The Skill uses verified Google Workspace identity and calls the Today OA API directly. It does not contain Slack, banking, OA service, or employee credentials.
