# Odessy roadmap

Start with a small fix and its test. The [contribution guide](../CONTRIBUTING.md#choose-where-to-start) points to the files and commands. GitHub tracks the status of each issue.

| Milestone | Work | Done when |
| --- | --- | --- |
| 1. First run and first PR | Make Windows launch easy to find. Make [empty recorded Output (#72)](https://github.com/Trisoma/odessy/issues/72) understandable. | A new contributor can open the app, verify an approved local action, and find a file and test for a small change. |
| 2. One dependable remote task | Clarify [SSH login and metrics (#70)](https://github.com/Trisoma/odessy/issues/70). Use an existing training log as the first everyday example. | Record a typed run with the target, approved read, matching log lines and a reconnect after restart. |
| 3. Voice for the same task | Improve [Piper diagnostics (#69)](https://github.com/Trisoma/odessy/issues/69) and [TTS timing summaries (#71)](https://github.com/Trisoma/odessy/issues/71). | A contributor can identify the slow or failing stage and reproduce the timing comparison. Repeat the log task by voice and record the microphone, speaker and wake checks on the devices used. |

I want to ask Odessy, “Did my training job finish? Show me the lines that tell you.” Start with an existing log on a connected computer. Approve a read of the last few lines and compare the answer with the log. Get that working in typed chat, then try the same task by voice. This is the workflow we plan to verify.

## Later

[Role-based dispatch (#73)](https://github.com/Trisoma/odessy/issues/73) is a proposal. New runtimes, MCU/BLE control, wider desktop packaging and automatic update discovery are outside these milestones. Revisit them when someone can name a user need, an owner and a check.

[Current support](current-status.md) · [Path to area ownership](../CONTRIBUTING.md#path-to-area-ownership)
