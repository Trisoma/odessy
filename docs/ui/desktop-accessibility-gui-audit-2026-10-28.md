# Odessy desktop accessibility GUI audit — 2026-10-28

## Scope and identity

This is a partial GUI acceptance audit, not an implementation or release signoff. The observed existing window belongs to `C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\odessy\apps\desktop\build\windows\x64\runner\Release\odessy_desktop.exe`. File version and product version: **0.5.0+1**; SHA-256: `4A96EE02E4535056F57116ADD5C71B449DD7B612F36CBC655495EEE5C12EE70C`. Version metadata identifies the executable wrapper; it does not independently establish the identity of every accompanying build asset. Windows reports `10.0.26200.0`; edition and display scaling were not established. Existing local connection showed **CONNECTED**, a loopback address and a startup model; this is a live connected session, not demo evidence. No message was sent to the model. Monitoring already returned HTTP 503; no service was deliberately stopped.

The initial captured window was 1187 × 843 logical screenshot pixels, dark theme. A narrow 773 × 843 window and light theme were inspected; dark theme was restored and the window was returned approximately to its original size (last confirmed capture 1176 × 843). Scaling and Windows text size were not changed or verified. Temporary unsaved workspace edits were reversed to the originally displayed value; no settings were saved. The app remained connected. Captures are evidence of this run; green glow is the automation pointer, not an application focus indicator.

## Results matrix

Pass is limited to the stated observation; it does not cover all controls or states. NV = not verified.

| Area | Result | Evidence and limits |
|---|---|---|
| Four main destinations | Pass, sampled | Tab and Enter/Space opened Assistant, Workspaces, Devices and Settings. |
| Every visible control reachable, focus visible, visual order, no traps | NV | Sampled controls only. TTS focus eventually scrolled into view after animation. Browse visual affordance failed (D1). No exhaustive traversal or trap survey. |
| Dialog initial focus and return | Fail | Unsaved dialog opened and returned from Escape with accessibility focus reported on a generic pane, not its action or triggering Settings button (D3). |
| Dialog containment and Escape | Pass, sampled | Tab cycled Keep editing → Discard → Save → Keep editing; Escape closed it and retained the temporary edit. Native folder picker Escape returned focus to Browse workspace. Reverse traversal and other dialogs remain NV. |
| Dirty navigation choices | Pass, sampled | A temporary workspace edit triggered Save / Discard / Keep editing. Individual Save and Discard outcomes were not executed. Contradictory status after manual reversion is D2. Settings section-specific dirty paths remain NV. |
| Form edit / validation | NV | Workspace field accepted a reversible keyboard edit; original text restored. Validation and error association were not tested. |
| Save, error and loading keyboard recovery | NV | No save or intentionally induced service failure. Existing monitoring HTTP 503 left Settings and workspace accessible; this does not qualify other failure states. |
| Approval, clarification, exact command, target, scope, response channel and Stop | NV | No fixture card was successfully opened; no consequential approval or paid request was made. |
| Light/dark and narrow layout | NV | General Settings and TTS were visually sampled at 773 × 843 and in both themes; not a full-screen or long-path audit. Browse affordance failed (D1). No enlarged text or changed scaling. |
| Screen-reader names, roles, state, validation and announcements | NV | No Narrator speech or transcript was observed. Windows accessibility-tree inspection is not a screen-reader test. |
| Voice state and physical hardware | NV | Assistant visually said voice unavailable; no microphone or playback was used. |
| Service recovery | NV, partial observation | Devices displayed monitoring unavailable (HTTP 503), retained values historical, and provided refresh; Settings remained accessible. Recovery from a failed coordinator or save was not induced. |

## Reproduced defects

### D1 — P2: Browse controls have no visible icon or label

**Steps:** In the connected dark-theme window open Workspaces. Focus the workspace path field and press Tab. The accessibility tree reports `Browse workspace`; press Enter to open the native folder picker, then Escape. In narrow light-theme Settings → Text to speech, tab from the kokoro model field to `Browse kokoro model`.

**Expected:** Each Browse action is visibly recognizable and its focused state identifies the action. **Actual:** the end of the workspace field is visually blank until focus shows an empty grey circle; the TTS model Browse control similarly shows an empty circle with no discernible glyph or label. The workspace action did open the folder picker, and Escape returned focus. **Impact:** sighted mouse and keyboard users may not discover folder/file selection or know what the focused control does. Evidence: [workspace Browse focused](desktop-accessibility-browse-2026-10-28.png). The TTS observation is a separate visual sample, not Narrator evidence.

### D2 — P2: Contradictory unsaved-change status after restoring an edit

**Steps:** Open Workspaces; append `x` to the existing workspace path. Attempt to navigate to Settings; press Escape in Unsaved changes. Return to the field and remove `x` so the original value is restored. Wait for the UI to settle.

**Expected:** A single consistent status reflects the restored, unchanged section. **Actual:** the section says `No unsaved changes in this section.` and its Save button is disabled, while `Unsaved changes.` remains visible below it. The contradictory message persisted after navigating away and back during this session. **Impact:** users cannot confidently tell whether their work is saved or whether action is needed. Evidence: [contradictory status](desktop-accessibility-status-2026-10-28.png). No saved configuration was changed.

### D3 — P2: Unsaved dialog lacks identifiable initial and restored keyboard focus

**Steps:** Append a character to the workspace field; select Settings to open Unsaved changes. After the dialog settles, inspect the focus and press Tab through its buttons; press Escape.

**Expected:** Focus initially lands on a safe dialog action or identifiable dialog content, stays within it, and returns to the navigation trigger when cancelled. **Actual:** on opening, the automation accessibility focus reported a generic pane and no action had a visible focus highlight. Tab then focused Keep editing, Discard, Save, and wrapped to Keep editing. Escape closed the dialog without discarding the edit; after settling focus was again reported on a generic pane rather than Settings. This observation does not establish what Narrator announces. **Impact:** keyboard users begin and return without a clear focus location and must rediscover their position. Evidence: [initial dialog](desktop-accessibility-dialog-2026-10-28.png). The test edit was manually reversed after Escape.

## Evidence boundaries

**Actual GUI observations:** the matrix and defects above. The local service was connected; no provider inference, real approval, pairing, revocation, service removal, recording or playback was performed. Monitoring's existing 503 is an observed unavailable state, not a deliberately created outage. The native picker opened and cancelled without selecting a folder.

**Actual Narrator observations:** none. This environment did not provide a reliable way to hear or capture Narrator speech, so Narrator was not run and no announcement is claimed. Accessibility-tree names and focused elements were observed via the GUI automation interface only. They cannot establish spoken names, state, live-region behavior, duplicate speech, or error announcements.

**Source-derived context:** `apps/desktop/lib/main.dart` contains an explicit `--demo` branch and the conversation UI identifies demo fixtures `/approval` and `/question`. An attempt to launch a separate `--demo` instance did not yield a separately identifiable demo window; the existing connected session was not switched or disrupted. Therefore no demo card was observed or treated as live evidence. Prior audit and backend acceptance documents provide context only; their historical findings and automated results are not reproduced claims here.

**Assumptions:** No assumption is made that the executable version uniquely identifies all adjacent Flutter assets, that the current service is representative of a fresh installation, or that an accessible name implies an audible Narrator announcement.

## Human follow-up procedure

On a disposable or explicitly approved test setup, turn on Windows Narrator and record what is actually heard (or a trustworthy speech transcript), Narrator version/settings, and exact steps. Navigate the four destinations using Tab/Shift+Tab, Enter/Space and arrows; confirm names, roles, selected/disabled states and focus return. Edit an invalid field and listen for its label and error; trigger non-destructive connection, save/error and voice-state changes and listen for timely, non-duplicated announcements. In an explicitly labelled demo instance, use `/approval` and `/question`; verify action, exact command, target, scope and permitted response channel are announced distinctly, with Stop separate and no rollback promise. Cancel ordinary dialogs with Escape and confirm focus containment and return. Do not approve real consequential actions or record/play audio without consent.

Repeat the full keyboard and long-command/path checks at normal and narrow sizes, both themes, enlarged text and declared Windows scaling, restoring OS settings afterward. Test save/loading/failure with disposable configuration and controlled unavailable services. Verify clean-machine installed/portable startup, physical audio and hardware, two-computer pairing, and external users independently. Credentials, a second computer, approved hardware and real provider access were not available or authorized for this run. Windows GUI observations do not qualify other platforms. The broader UX plan is not declared complete.

## Concise fixes for Codex

1. Restore a visible, adequately contrasted Browse glyph or text for workspace and path pickers in both themes; retain a meaningful accessible name and visible focus.
2. Recompute or clear the stale unsaved status when the edited value returns to its saved value; keep section and global messages consistent across navigation.
3. Place initial focus on a safe action or dialog heading, retain the initiating focus target, and restore it after Escape/Keep editing. Cover forward/reverse cycling and cancellation with native GUI and screen-reader checks.

These fixes follow only the reproduced observations above; remaining NV cases need acceptance evidence before conclusions.
