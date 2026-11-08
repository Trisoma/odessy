# Installer integration and in-app updates

Recorded November 7, 2026 on `develop`.

**Status: future-work requirements.** This document and `AGENTS.md` add guidance
only. Automatic feature packaging checks and an in-app automatic updater are not
implemented by this change.

## Goal

Every released installer must contain the intended application version and its
required components. A feature that works in a checkout must also work after
installation, without relying on a developer's Python installation, environment
variables, source tree or previously installed optional packages.

Add a dedicated **Settings → Updates** area. Users should be able to update an
installed Odessy without rebuilding it or manually reinstalling each release.
In automatic mode, Odessy should discover, download and safely apply newer
compatible published versions, with clear status and restart information.

## One release pipeline for application and installer

- Use one release definition for the version, source commit, platform,
  architecture, Python version, dependency locks and component manifests.
  Application binaries, Python wheels, bootstrap helpers and the installer must
  all identify that same release. Include native components where applicable.
- Build fresh inputs from that commit in a clean build environment. Stage outputs
  in a unique release directory. Cached dependencies may be reused after hash and
  compatibility verification; stale application binaries must not be reused.
- Package ordinary application changes automatically through the shared source
  and resource inventory. Declare optional components and their dependencies in
  a shared manifest rather than maintaining disconnected feature lists in the
  UI, installer and runtime. Adding an optional component must update that
  manifest, its verification and the corresponding user choice.
- Include resource files, migrations, helper code, runtime dependencies, license
  notices and component metadata. Reject mismatched Python/wheel ABIs and missing
  required components before producing an installer.
- Produce a build receipt containing version, commit, component versions and
  artifact hashes. Expose installed version/build identity in the application and
  make it possible to compare it with the installer receipt.
- CI should build release candidates from the selected revision, install them
  into an empty folder and compare installed code/resources with the receipt.
  Publication to an update channel remains a separate release operation.

Existing integration points include `tool/build_setup_bundle.py`,
`tool/package_native_setup.py`, `installer/build/context.py`, the setup component
manifests, `src/odessy/setup/` and the desktop setup controllers. Reuse and extend
these paths; do not create a second installation engine for updates.

## Settings → Updates

The page should show:

- Installed version and build identity, selected release channel, latest
  compatible published version and time of the last check.
- Check now, release notes, download/install progress, retry and restart actions.
- Automatic-update controls, including pause/manual mode and clear disclosure
  of when updates can be applied.
- Honest checking, current, available, downloading, deferred, offline,
  unsupported and failed states. A failed check must not display “up to date.”

“Latest” means the latest trusted published version for the selected channel,
operating system and architecture. It does not mean an arbitrary `develop` head
or the newest executable found on disk. Stable users must not silently move to
beta/unstable builds. Match Jetson ARM64, Windows x64 and other supported targets
to their respective qualified artifacts; never substitute another platform.

## Automatic update behavior

1. Check a trusted release feed without blocking startup, configuration or chat.
   Authenticate release metadata and artifacts; a checksum alone does not prove
   the publisher's identity. Define signing keys and key rotation before shipping.
2. In automatic mode, download the compatible release in the background with
   bounded retries, cancellation, disk-space checks and verified completion.
   Preserve component opt-ins; an application update must not silently download
   newly introduced voice/wake models or enable previously disabled features.
3. Prepare the existing lifecycle engine's exact update plan for the intended
   installation and verified release. Define explicit, bounded automatic-update
   consent; retain plan-ID revalidation and installation locking rather than
   bypassing the existing review-before-apply contract.
4. Defer activation during active conversations, approvals, recording, execution
   jobs or another installation operation. Show what is waiting and why. Preserve
   drafts and session continuity, and explain when a restart is required.
5. Stage and verify the new version before changing the active version. Preserve
   credentials, settings, workspaces, usage data, user-owned models, modified
   retained files and permissions. Restart using the new installed executable and
   isolated runtime, then verify local-service health and report the result.
6. Keep recovery possible after an interrupted download, failed activation or
   power loss. Coordinate backups and database-schema compatibility. Do not
   promise rollback across an irreversible migration; defer unattended updates
   that cannot be applied and recovered safely.

Automatic application updates must not silently expand tool permissions, grant
new execution authority or change provider credentials, sessions or targets.

## Required acceptance checks

- Fresh installation from the final installer, with no checkout on the runtime
  path and no reliance on previously installed voice libraries or models.
- Upgrade from the previous released build while preserving configuration,
  credentials, component opt-outs, user files and data.
- Actual installed version/module-path and artifact-hash checks, including cold
  Windows audio startup. Verify optional dependency readiness independently.
- Offline checks, corrupt/untrusted packages, incompatible targets, insufficient
  disk space, concurrent updates, cancellation and interrupted recovery.
- Deferred activation while work is active, restart/handoff into the new version,
  migrations and supported rollback behavior.
- Flutter formatting, analysis, desktop tests and contracts validation, plus
  relevant backend/client/installer tests. Record remaining native, microphone,
  hardware and live-provider qualifications explicitly.

## Delivery checklist

- [ ] Shared release definition, build receipts and compatibility validation.
- [ ] CI checks proving installer contents match the selected source revision.
- [ ] Trusted release feed, signing and platform/channel artifact selection.
- [ ] Update service/controller integrated with the existing lifecycle engine.
- [ ] Settings → Updates page and automatic-update policy.
- [ ] Native fresh-install, upgrade, restart and recovery acceptance evidence.

Related: [existing installer wiring](existing-installer-wiring.md),
[native lifecycle](native-lifecycle.md), [voice components](voice-components.md).
