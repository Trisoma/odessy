# Odessy desktop UI

A visual guide to the current production Flutter interface for developers. Click any screenshot to view it at full size. The gallery covers the main page, navigation, workspaces, history, agent tools and **all eight settings groups**. File and folder names describe screens rather than internal implementation tasks.

[Conversation](#conversation-and-history) · [Other pages](#navigation-pages) · [Settings](#settings) · [Workspace panels](#workspace-and-agent-panels) · [Verification](#capture-and-verification)

## Main page

![Odessy main conversation in dark mode](conversation/main-dark.png)

## Conversation and history

| | |
|---|---|
| **Main conversation - light**<br>[![Main conversation - light](conversation/main-light.png)](conversation/main-light.png) | **Projects, nested chats and selection**<br>[![Projects, nested chats and selection](conversation/projects-and-recent.png)](conversation/projects-and-recent.png) |
| **Saved history and search**<br>[![Saved history and search](conversation/history.png)](conversation/history.png) | **Narrow conversation - dark**<br>[![Narrow conversation - dark](conversation/narrow-dark.png)](conversation/narrow-dark.png) |
| **Narrow conversation - light**<br>[![Narrow conversation - light](conversation/narrow-light.png)](conversation/narrow-light.png) |  |

## Navigation pages

| | |
|---|---|
| **Activity and usage**<br>[![Activity and usage](navigation/activity-and-usage.png)](navigation/activity-and-usage.png) | **Devices**<br>[![Devices](navigation/devices.png)](navigation/devices.png) |
| **Workspace configuration**<br>[![Workspace configuration](navigation/workspaces.png)](navigation/workspaces.png) | **Jobs**<br>[![Jobs](navigation/jobs.png)](navigation/jobs.png) |
| **Handoffs**<br>[![Handoffs](navigation/handoffs.png)](navigation/handoffs.png) |  |

## Settings

| | |
|---|---|
| **General and appearance**<br>[![General and appearance](settings/general.png)](settings/general.png) | **Models and connections**<br>[![Models and connections](settings/models-and-connections.png)](settings/models-and-connections.png) |
| **Speech to text**<br>[![Speech to text](settings/speech-to-text.png)](settings/speech-to-text.png) | **Text to speech**<br>[![Text to speech](settings/text-to-speech.png)](settings/text-to-speech.png) |
| **Voice**<br>[![Voice](settings/voice.png)](settings/voice.png) | **Devices and tool access**<br>[![Devices and tool access](settings/devices-and-tool-access.png)](settings/devices-and-tool-access.png) |
| **Advanced**<br>[![Advanced](settings/advanced.png)](settings/advanced.png) | **Installation**<br>[![Installation](settings/installation.png)](settings/installation.png) |

Additional states: [local pairing controls](settings/local-pairing.png) and the [native Windows window](settings/native-window.png).

## Workspace and agent panels

| | |
|---|---|
| **Workspace inspector**<br>[![Workspace inspector](workspace/inspector.png)](workspace/inspector.png) | **Used context**<br>[![Used context](workspace/context.png)](workspace/context.png) |
| **Project memory**<br>[![Project memory](workspace/memory.png)](workspace/memory.png) | **Documents and citations**<br>[![Documents and citations](workspace/documents.png)](workspace/documents.png) |
| **Local terminal**<br>[![Local terminal](workspace/terminal.png)](workspace/terminal.png) | **Pending terminal approval**<br>[![Pending terminal approval](workspace/terminal-approval.png)](workspace/terminal-approval.png) |
| **Checkpoints - dark**<br>[![Checkpoints - dark](workspace/checkpoints-dark.png)](workspace/checkpoints-dark.png) | **Checkpoints - light**<br>[![Checkpoints - light](workspace/checkpoints-light.png)](workspace/checkpoints-light.png) |
| **Narrow tools layout**<br>[![Narrow tools layout](workspace/narrow-tools.png)](workspace/narrow-tools.png) |  |

## Capture and verification

These are actual rendered production screens, not generated mockups. Connected captures use the real authenticated local Python service with explicitly deterministic model replies and an isolated laptop workspace. No paid provider, microphone or remote device is required. Unavailable/empty states are shown honestly; screenshots do not claim live hardware, real-provider or human acceptance. The separately labeled native window capture is disconnected.

The main entry point is `apps/desktop/lib/main.dart` / `OdessyApp`. The standalone UI demo is not used. Tools/installation are in Settings; Activity & usage remains a navigation page, while detailed activity diagnostics belong in inspector Output.

See [the source/artifact/screenshot manifest](verification/manifest.json) and [capture instructions](verification/README.md). The latest gallery capture regression passed. This folder keeps only this current screen-organized set.
