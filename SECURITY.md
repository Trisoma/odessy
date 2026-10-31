# Security policy

## Supported versions

Only the latest pre-release receives best-effort security fixes. Older releases
and development snapshots are unsupported. Odessy is an unstable developer preview.

## Private vulnerability reporting

Use GitHub Private Vulnerability Reporting: open this repository's **Security**
tab, then **Report a vulnerability**. Do not disclose vulnerabilities in public
issues. Maintainers must enable private reporting before making the repository public.

Maintainer contact: [jaimefine6@gmail.com](mailto:jaimefine6@gmail.com).

Include the affected version/commit, platform, deployment mode, reproduction steps,
impact and redacted diagnostics. Never include real API keys or private data.
Maintainers will acknowledge, investigate and coordinate disclosure on a best-effort
basis; no response or fix deadline is guaranteed.

## Scope

In scope: bypasses of policy, confirmation, workspace authority, authentication,
credential isolation or the confinement boundary actually reported by the host;
credential disclosure; and exploitable dependencies in the supported preview path.
Reports affecting experimental modes are welcome, with their mode identified.

Out of scope as new vulnerabilities: the documented limitations below on their own,
unsupported versions/platforms, model quality and ordinary setup or feature bugs.
Use public issues for non-security bugs. A bypass beyond a documented limitation
can still be a security issue; report it privately when unsure.

## Known insecure-by-design modes and limits

- The experimental LAN executor uses unencrypted HTTP. Its bearer credential can
  be observed on the network and used to call tools directly, bypassing coordinator
  confirmation. Use only a trusted LAN or point-to-point link.
- `ODESSY_MCP_HOST=0.0.0.0` exposes an unauthenticated command executor on every
  network interface. Do not expose it to untrusted networks. Prefer loopback with
  an SSH tunnel, or authenticated Rust/mTLS `odessy-link` pairing.
- Windows command isolation is a partial ACL boundary. Jetson Landlock is off and
  most confinement backends are unavailable. Without Node.js, `terminal.run` is
  unconfined. Odessy is not a production network service.

See [limitations](LIMITATION.md), [network setup](README.md#two-machine-setup-jetson--laptop)
and [host pairing](docs/reference/host-pairing.mdx).
