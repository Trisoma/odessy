# History recovery audit — 2026-10-20

Audited `develop`: `80ee9b3c7ba560f99e15ad1359511c27dc5a1f6a`.
The live remote `develop` tip matched this revision during the audit.

## Result

All useful displaced implementation changes identified in the retained history
are accounted for in current `develop`. No source patch needs to be restored.
Old revisions and seven previously unreachable stash commits have been preserved
under local `rescue/*` branches. No existing shared branch was reset, rebased,
merged, or pushed during this recovery.

This report lives on `fix/history-recovery-audit`, based on current `develop`, in
the separate `odessy-history-recovery` worktree. Merging this report is optional;
it does not repair or change runtime behavior.

## Recorded branch rewrites

Dates below use Asia/Shanghai time. These are non-fast-forward transitions found
in the retained remote-tracking reflogs, not ordinary branch checkouts.

| Date and time | Branch | Previous tip | Replacement tip | Assessment |
| --- | --- | --- | --- | --- |
| October 18, 19:44 | `feat/single-machine` | `36cca20` | `6acf109` | Rebased implementation survives; README changes retain newer version/cloud documentation. |
| October 18, 23:01 | `fix/eval-findings` | `ad1c26e` | `73c4a02` | Evaluation fixes survive. Range comparison shows preserved newer deployment/provenance metadata context. |
| October 19, 12:38 | `feat/cloud-focus` | `9f5be56` | `fd895d8` | Cloud changes survive with newer model budgets, attempt timeout, and metadata/reporting. |
| October 20, 12:01 | `develop` | `f8ac28f` | `4ae10a0` | Old tip included an accidental concurrency merge and its revert; their net tree change was empty. Later develop contains the refactor and concurrency implementation. |
| October 20, 12:03 | `refactor/package-layout` | `a3a7dc1` | `58c6f12` | Refactor survives; trees differ only in newer evaluation wording in `LIMITATION.md` and `docs/releases/v0.4.0.md`. |

Patch/tree comparisons supporting these conclusions:

- `git diff 1ec2392 f8ac28f` is empty: the old develop merge/revert pair
  introduced no net file change. Restoring the revert would remove wanted work.
- `cfc1329` and `05abe5f` have identical trees and patch-equivalent concurrency
  changes. `145a5b1` and audited develop also have identical trees.
- Eval commits `f7ea0ee` / `c10be40` and `ad1c26e` / `73c4a02` have identical
  patches. The earlier eval patch was adapted to retain newer metadata context.
- `05eccb0` differs from audited develop only in the two evaluation documentation
  files above.

## History before October 18

No branch-history overwrite before October 18 was found in retained local and
remote-tracking ref transitions. Earlier non-ancestor transitions in `HEAD` were
branch checkouts, not evidence of overwritten branch tips. All commit objects
named by the retained raw reflogs were present.

This conclusion is limited to this clone's retained evidence. Expired reflogs,
unfetched remote commits, and history available only in another clone cannot be
ruled out. A remote branch deleted after a merge is not itself evidence of lost
implementation.

## Additional objects recovered

`git fsck --full --no-reflogs --unreachable` identified seven unreachable commits,
all stash snapshots dated October 18–20. Each is now pinned by a branch named
`rescue/stash-object-<short-sha>-20261020`.

| Commit | Contents | Treatment |
| --- | --- | --- |
| `0821d52` | Eval provenance work-in-progress | Preserved; changes subsequently committed in `6c04d03` and retained today. |
| `83d91b8` | Associated stash index snapshot | Preserved; no additional patch to apply. |
| `43498f2` | Untracked `pytest-full.log` | Preserved as historical test output, not added to runtime code. |
| `5e53601` | Incomplete package-refactor stash | Preserved; completed refactor already survives. Applying this stash would reintroduce deletions without all relocated files. |
| `a2019e1` | Associated refactor index snapshot | Preserved; no additional patch to apply. |
| `b83f1ba` | v0.3.0 release documentation stash | Preserved; its contents survive in `4fce98b`, with 24 additional roadmap lines in the committed version. |
| `cf0633f` | Associated release-documentation index snapshot | Preserved; no additional patch to apply. |

The older displaced tips are preserved as:

- `rescue/develop-before-force-20261020` → `f8ac28f`
- `rescue/cloud-focus-before-force-20261020` → `9f5be56`
- `rescue/package-layout-before-force-20261020` → `a3a7dc1`
- `rescue/develop-current-20261020` → `80ee9b3`
- `rescue/single-machine-before-rebase-20261018` → `36cca20`
- `rescue/eval-findings-before-rebase-20261018` → `ad1c26e`
- `rescue/concurrency-rebased-intermediate-20261020` → `cfc1329`
- `rescue/develop-intermediate-merge-20261020` → `145a5b1`

A complete bundle is saved outside the checkout at
`../odessy-recovery-complete-20261020.bundle`. Keep a separate copy of this file;
the rescue branches and bundle have not been uploaded to GitHub.

## Verification

Focused regression checks on the audited develop checkout:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evals.py tests/test_eval_findings.py tests/test_deployment.py tests/test_single_machine.py tests/test_cloud_model.py tests/test_package_layout.py -q --tb=short
```

Result: **277 passed in 33.73 seconds**. This checks the affected evaluation,
deployment, cloud adapter, and package layout paths; it is not a full-suite or
hardware validation. No paid cloud evaluation was run.

## Safe next step

Continue feature work from current `develop`. Do not cherry-pick the old stacks
or reapply the old concurrency revert merely to make the historical commit IDs
appear again. The useful content is already present, and the original objects
remain available through the rescue refs and backup bundle.
