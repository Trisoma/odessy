# Installer and installed-application integration

For feature changes, bug fixes, dependency changes and release builds, read
[the installer and updates plan](docs/setup/installer-release-and-updates-plan.md).
Follow more specific `AGENTS.md` files in their respective directories.

- Consider the installed application as part of the feature. Include required
  code, resources, dependency wheels, helper modules and migrations in the
  packaging changes; a source-checkout test alone is insufficient.
- Build the application, Python wheel, bootstrap/helper and installer from the
  same intended commit. Do not silently reuse stale release folders or test apps.
- Verify that bundled Python, wheel ABI, operating system and architecture match.
- Exercise the feature through a fresh installation or an upgrade as relevant.
  Confirm the actual installed executable/module paths and preserve user data,
  credentials, component choices and permissions.
- Record exactly which artifacts and checks were verified, and explicitly note
  unavailable hardware/provider checks. Never claim a development preview proves
  that the installer contains the change.
- The planned in-app Update section and release automation are future work.
  These instructions do not implement them or authorize a push, publication,
  automatic merge or changes to the user's existing installation.

This file guides agents; enforceable build checks must also be implemented in
the packaging tools and CI as described in the plan.
