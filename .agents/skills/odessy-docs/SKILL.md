---
name: odessy-docs
description: Write or review Odessy docs, examples, user-facing text, translations, and code comments. Use concrete steps and the maintainer’s direct voice.
---

# Write Odessy docs

Start with the reader’s task. Give the command or step, where to run it, and what success looks like. Say why you chose a command, example, or default when that helps the reader decide. Give the reason enough room to be clear. Link to the guide that owns the rest.

Use first person for the maintainer’s welcome or a recorded observation. Use “you” and active verbs for instructions. Fix grammar and keep the writer’s choices. Keep private anecdotes and samples out of public docs. Stop when the reader can act.

Check changed facts against source, tests, or the exact release artifact. Put the relevant permissions, target identity, credential handling, platform limits, and cost beside the step they affect. Label simulated, source, installed, and hardware evidence accurately. Keep historical receipts and license text intact.

Use the English page as the canonical source. Update its Chinese and Spanish counterparts together. Compare copied commands byte for byte with their source and across translations. Check paths, backslashes and quoting in the rendered text. Keep model IDs intact. Check relative links, translated anchors, images, and the run, change, and test routes.

Follow the MLX quick start’s structure. Lead with a runnable example and its expected result. Put setup, diagnostics, and reference details where readers need them. Check that a newcomer can find how to run it, which file to change, and the test command and working directory.

In code comments, explain the reason behind a guard, ordering rule, or tradeoff. Remove comments that repeat the code. Preserve API contracts, directives, test rationale, and strings that affect behavior.

In contributor docs and roadmap updates, welcome focused CI and behavior-preserving refactors. Link existing issues to milestones with a finish condition and say what can wait. Before proposing a new runtime or optional integration, name the everyday task it helps, who can maintain it, and how to verify it.

Example: “Open **Output** after the command runs. Check the exit code and compare the hostname with your laptop.”
