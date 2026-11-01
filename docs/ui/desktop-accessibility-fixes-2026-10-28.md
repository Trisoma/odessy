# GUI audit fixes — October 28, 2026

Follow-up to [the partial native GUI audit](desktop-accessibility-gui-audit-2026-10-28.md).
The original report and screenshots are retained unchanged.

| Finding | Implemented correction | Regression evidence |
|---|---|---|
| D1: blank Browse circles | Component path fields use a visible, bordered **Browse…** text button. Contextual tooltips, keyboard activation and existing native pickers remain. Discoverability no longer depends on an icon-font glyph. | Light/dark rendering at 840 logical pixels and 200% text, with explicit text/tooltip assertions; widget screenshots inspected. |
| D2: stale unsaved status | Status follows the actual section draft. Restoring the baseline or discarding clears the unsaved message. Empty workspace edits retain their field in the draft, so reversion compares the same shape and clearing cannot silently become an omitted edit. | Controller and real editor regressions cover edit/revert/discard and consistent clean-section messages. Backend validation remains authoritative for an empty submitted path. |
| D3: missing initial/return focus | Both settings leave dialogs autofocus **Keep editing**, use closed-loop traversal and restore their origin. Main navigation has app-owned focus nodes and focuses the trigger before opening the dialog. Narrow drawers stay open when navigation is cancelled. | Keyboard tests at 1280 and 840 logical pixels cover initial focus, forward/reverse wrap, Escape, Keep editing, restored trigger and retained edits. |

Fresh full Flutter suite: **166 passed, 7 skipped**. No provider calls, audio
capture, real settings saves or permission changes were performed. The preceding
direct-launch fixes and unrelated work were preserved; no Git branch changes,
commits or pushes were made.

Flutter analysis: **no issues**. Windows release rebuild: **passed** (40.8 seconds).
Independent read-only review found no significant behavioral regression.

Widget screenshots: `apps/desktop/build/preview/gui-audit/browse-dark-200.png` and
`browse-light-200.png`. They are rendered widget evidence, not native Windows or
Narrator acceptance. The original report's unverified native/screen-reader,
hardware, enlarged OS scaling and external-user cases remain unverified.

Reopen the rebuilt Windows executable and repeat D1–D3 from the original report
for native acceptance. No reinstall or configuration reset is required.
