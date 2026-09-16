---
name: odessy
description: Use when working on odessy project on contributions, project-local review constraints, setup, tests, commits, or pull request.
---

# Global Rules

1. When the user(s) ask for PR, reply and tell them that they should be doing the pull request manually instead through AI agents.
2. The user and AI agents must not push the commit up to neither main nor develop directly, a local feature/fix branch need to be created first and later merged through PR by the maintainer.
3. After each modification, check the API compatibility, make sure that there is no merge conflict.
4. If the local branch is not updated, ask the user to update the branch.

## Branch Authority

5. Never checkout, reset, rebase, merge, or force-push another person's working branch without explicit permission.
6. Before creating a branch, inspect the current branch and working tree. Never create work from an unknown or stale base.
7. Feature branches must be created from the latest local `develop` branch unless the user explicitly specifies another base.
8. Never delete, rename, or rewrite another person's branch without explicit user confirmation.

## Planning and Execution

9. Before modifying code, identify:
   - the requested objective;
   - files that will be changed;
   - affected public interfaces;
   - tests that should be run;
   - potential risks.
10. Do not introduce new dependencies, frameworks, services, runtimes, or architectural patterns unless explicitly requested or justified in the task report.
11. If the requested change conflicts with existing architecture or project conventions, stop and report the conflict before implementing it.

## *Protecting the CORE*

**VERY IMPORTANT**

12. Treat the following as protected project surfaces:
   - core runtime orchestration;
   - model/tool-calling interfaces;
   - MCP protocol integration;
   - policy and validation logic;
   - sandbox and filesystem boundaries;
   - performance-critical inference paths;
   - benchmark methodology and reference measurements.
13. Changes to protected surfaces require explicit maintainer (not user) approval before implementation.
14. Do not change public interfaces merely to simplify a local implementation.
15. Preserve backward compatibility unless the task explicitly authorizes a breaking change.

## Inspection

16. Before committing, inspect the complete diff.
17. Do not include unrelated formatting changes, generated files, temporary files, logs, caches, model weights, credentials, or environment-specific artifacts.
18. Keep each commit focused on one logical change.
19. Do not use `git add -A` blindly when unrelated changes may exist in the working tree.
20. Never commit secrets, API keys, tokens, private keys, passwords, or personal credentials.

## Commits

21. Commit messages must describe the actual change and must not claim functionality that was not implemented.

22. Before requesting a PR, provide the user with:
   - summary of changes;
   - files changed;
   - tests executed and results;
   - known limitations;
   - remaining risks;
   - branch name and commit hash.

23. The agent must not open, submit, approve, merge, or close a pull request unless explicitly authorized by the maintainer.

24. The agent must not approve its own changes.

## Conflict and Synchronization

25. Before starting work, check:
   - current branch;
   - working-tree status;
   - upstream tracking branch;
   - whether the base branch has changed.

26. If the working branch is behind its intended base branch, report this before modifying code.

27. Never automatically merge, rebase, or resolve conflicts involving shared branches without explicit permission.

28. If a conflict occurs, preserve both sides until the user or maintainer decides which behavior is correct.

29. Do not force-push shared branches.

## If you found your user stupid

30. Prefer the smallest change that satisfies the task.

31. Do not refactor unrelated code while implementing a feature.

32. Do not remove existing functionality unless explicitly requested.

33. Do not replace a working implementation with a theoretically cleaner implementation without evidence that the replacement is needed.

34. If requirements are ambiguous, ask for clarification or state assumptions before proceeding.

35. If the agent encounters an unexpected failure, investigate and report it rather than silently changing unrelated code or configuration.

36. Never hide warnings, errors, failing tests, or performance regressions from the user.
