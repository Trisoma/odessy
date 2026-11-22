# SSH connections, saved chats and compact inspector

**Developer Preview · v0.5.0-alpha — source preview, November 17, 2026**

The [temporary SSH and connected-device gallery](direct-connect-20261117/README.md)
supersedes this page's earlier Review/Test/Save and password-dialog captures.
It includes the current one-step form and populated device overview/details.

These are actual Flutter renders of production components with deterministic
test APIs, not generated mockups and not proof of a live remote connection. The
shell captures are explicitly marked DEMO. The native image was captured with
Windows computer use from the rebuilt `main.dart --demo` executable in an isolated
profile, then that owned preview was closed. The SSH page uses a fixture service
and the production theme; its password dialog is captured before any text entry.
No credentials appear in the images.

| State | Capture |
| --- | --- |
| Native Windows source build (DEMO, isolated profile) | [Native window](native-source-demo.png) |
| Direct `user@hostname` entry and reviewed identity | [SSH computer](ssh-computer.png) |
| Masked, session-only password entry | [Password dialog](ssh-password-dialog.png) |
| GitHub authentication succeeds but no shell exists | [Git-only endpoint](ssh-git-only.png) |
| Saved chat opens directly | [Conversation](saved-chat.png) |
| Right-click management menu | [Conversation actions](chat-context-menu.png) |
| Right inspector, wide | [1600 px](right-inspector-20261117/right-inspector-1600.png) |
| Right inspector, compact desktop | [1182 px](right-inspector-20261117/right-inspector-1182.png) |
| Right inspector, narrow desktop | [960 px](right-inspector-20261117/right-inspector-960.png) |
| Right inspector, narrow window | [720 px](right-inspector-20261117/right-inspector-720.png) |
| Narrow layout, text scale 1.0 | [720 × 900](right-inspector-20261117/narrow-inspector-scale-1.0.png) |
| Narrow layout, text scale 1.3 | [720 × 900](right-inspector-20261117/narrow-inspector-scale-1.3.png) |
| Narrow layout, text scale 1.6 | [720 × 900](right-inspector-20261117/narrow-inspector-scale-1.6.png) |

Wide chat captures are 1440 × 1000; SSH captures are 1100 × 1000. Bundled fonts
and Material icons are loaded for capture. The manifest records image and source
hashes, scope and verification commands. The previous gallery remains a historical
reference for other unchanged pages; these captures supersede its affected states.
The `right-inspector-20261117` set supersedes the older bottom-inspector images
in this directory. It uses the same production components and deterministic
fixtures; it is source-preview evidence, not installed-app qualification.

From `apps/desktop`, with an existing resolved Flutter cache:

```powershell
$env:ODESSY_FIX_CAPTURE_DIR = (Resolve-Path ../../docs/view/ssh-history-inspector).Path
flutter test --no-pub test/saved_chat_navigation_test.dart test/ssh_connection_test.dart test/agent_basics_layout_test.dart
```

Widget tests check direct navigation/no replay, history-only behavior, right-click
management, masked input, explicit Git-only status and retained narrow composer
drafts at scales 1.0/1.3/1.6. Native launch, installed delivery, hardware and human
acceptance remain separate evidence. See [SSH setup](../../setup/ssh-computers.md)
and [history behavior](../../ui/conversation-history.md).
