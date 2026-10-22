# Protected client package

Pure Dart transport and public contracts only. No Flutter dependency, provider SDK,
agent loop, tool execution, local trust/database access or policy decisions.

Keep authentication scoped to the configured origin. Never put bearer tokens in
URLs, exceptions, logs, fixtures or ordinary persistent preferences. A memory-only
store is not an OS credential vault; persistent implementations must prove their
native protections separately.

Do not retry mutations automatically. Reconnection restores snapshots and event
position; it never resubmits model/tool work or approvals. Bind delayed operations
to their original service/session identity. Keep JSON/frame limits and deadlines.

Contract additions need fake and HTTP tests, malformed-input coverage and project
owner review. Run `dart analyze` and `dart test` before handoff.
