# Agent basics client handoff (06)

The production `OdessyApp` exposes **Saved history & tools** from the project
sidebar. Existing Chat, Activity & usage, Devices, Jobs, Handoffs, Workspaces and
all Settings destinations remain reachable. This is a client slice; 07 must mount
and verify the 04/05 services before advertising their capabilities.

## Contributor path

From `apps/desktop`, use the existing supported `flutter run -d windows` entry
point (`lib/main.dart`). Connect to the normal local Odessy coordinator through
the existing connection/configuration UI. Neither `tastecode_demo.dart` nor a
fixture fallback implements these features. Existing cached dependencies are
sufficient for the offline checks below; this slice adds no dependencies.

1. Open **Saved history & tools**. Choose a durable project or create one. History
   browsing, search and loading older messages perform reads only.
2. Select a saved chat. Rename, pin, archive, export and delete use the actual
   saved conversation ID/revision. Export presents the versioned JSON and an
   explicit **Copy JSON** action; it does not silently write a filesystem path.
3. For a new unbound saved chat, explicitly bind the currently authorized
   workspace, then Resume. Known workspace bindings cannot be reassigned.
   Harness history remains read-only. Resume never replays a tool or restores
   jobs, audio, approvals or expired grants.
4. The permissions picker displays the last acknowledged service mode. Its menu
   and tooltip expose effectiveness and workspace/target scope. A failed write
   invalidates the mode display and requires refresh; it never grants authority
   optimistically. Ask/auto/full affect Custom tool dispatch only. Jobs,
   handoffs, terminal creation and checkpoint restoration retain their separate
   service approval boundaries.
5. **Context** inspects only context actually used. **Memory** creates, edits,
   toggles and deletes explicit project facts. **Documents** indexes only after
   an explicit confirmation and shows source status, refresh/remove and grounded
   retrieval citations. An unavailable module remains visibly unavailable.
6. **Terminal** is separate from the inspector's recorded **Output**. Nothing
   starts on tab open. When a verified backend is advertised, explicit Create,
   exact input, bounded output refresh, resize, interrupt and close use that
   backend. Output refresh is manual; this is not a terminal emulator. Input is
   sent exactly as entered, including any newline. The audited 05 candidate now
   passes real Windows ConPTY gates with the authorized pinned dependency.
   Production Terminal remains disabled until 07 mounts and packages that owner.
7. **Checkpoints** lists/creates local Git checkpoints, previews paths/status and
   exclusions, then explicitly confirms the exact preview hash/revision before
   requesting restore. A service acknowledgment is required before success is
   shown. A restore does not undo external actions.

Existing process-bound workspace groups are retained. Their explicit **Copy label
to saved project** action copies only a label into durable metadata; it does not
move or replay messages, bind a workspace, transfer sessions or copy permissions.
No cross-engine history migration is provided. Submitting the sidebar search
with Enter searches saved transcripts; typing still filters the existing live
session catalog.

## Ownership and integration

`AgentBasicsClient` in `packages/odessy_client` owns immutable, versioned response
parsers and exact routes over the existing `LocalApi` authenticated HTTP owner.
`BasicsController` owns UI state, revisions, capability gates and epoch/service/
session checks. `BasicsPage` owns presentation and confirmations; it opens no
databases, workspace files, sockets or processes. Existing `InteractionCard`
handles real pending approvals while an advanced action awaits the service.

The shell retains the external conversation draft across panel navigation and
keeps drafts keyed by live session on selection. Existing voice release and
model selection behavior remains in its original controllers. A successfully
resumed durable session is adopted into the desktop's shutdown ownership;
selecting an unrelated existing live session does not claim ownership of it.

07 must integrate 04/05 sequentially, after writers stop, and supply:

- 04 registrars, extension migration, trusted scope/file resolvers and bounded
  context injection/used-context recording through `PersistentComposition`.
- Conversation deletion/export lifecycle behavior for summaries, usage rows,
  owned indexes and checkpoint associations. Same-database derived deletion must
  use the agreed transactional/FK strategy, not nested writes in a delete hook.
  Conversation deletion must retain arbitrary workspace documents, unrelated
  project facts and workspace Git objects according to the documented ownership.
- 05 local checkpoint service and its current trusted authorization callbacks;
  session/service resource close hooks. PTY capability remains false without a
  separately verified implementation and approved packaged dependency.
- Exact capability flags only after routes and runtime owners work. Keep the
  frozen version-1 envelope or deliberately revise both parser and contract;
  adding uncontracted capability fields will fail strict parsing.

Live inspection of the running 05 implementation found `ckpt_<32 hex>` IDs and
optional `files`/`bytes` counters. The client accepts and validates these explicit
additions, as well as the original six-field checkpoint envelope. 07 must record
this clarification in the shared contract and compare the final 05 receipt.
No 04/05 implementation files were edited by 06.

## Offline verification

The ordinary Flutter command is:

```powershell
flutter test --no-pub --reporter expanded
```

In this cached Windows worktree, `flutter.bat` has a Git PATH problem. The tested
equivalent uses the installed Dart executable and `flutter_tools.snapshot` with
the Dart SDK `bin` on PATH. Run Flutter tools sequentially; 06 owns this slot.
See the pack's `doc/reports/06.md` for exact local commands and results.

`test/agent_basics_client_ux_test.dart` covers browsing without runtime creation,
pagination, revision/error handling, late receipts, restart invalidation,
read-only Harness, advanced capability gates, terminal/checkpoint identities and
wide/narrow layouts. `agent_basics_connected_preflight_test.dart` uses a real
authenticated loopback Python application with a clearly isolated fake model,
temporary profile and ephemeral port. It exercises production shell sending,
durable history/search/mutations/export/deletion and draft preservation.

Source/widget checks are separate from a native PTY, real model/provider,
installer/upgrade, hardware, hosted CI and human visual acceptance. Those are not
established by this worker's client tests.
