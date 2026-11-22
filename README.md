# Odessy

**English** | [简体中文](README.zh-CN.md) | [Español](README.es.md)

**Developer Preview · v0.5.0-alpha**

Hi, this is Odessy, not Odyssey! ⛵

> Curious how this started? [Read the story behind Odessy](https://github.com/Trisoma/odessy/blob/develop/Story.md).

I’m building Odessy because I want to talk to my computer and ask it to do useful work, like JARVIS or FRIDAY. Today, Odessy is an experimental Windows desktop agent. It can use a local or cloud model, run approved actions on your laptop, and send a scoped command to a connected computer.

The model proposes actions. Odessy’s service checks permissions before it runs them. [How it works](docs/ARCHITECTURE.md)

## Try Odessy

If you want to try Odessy on Windows, start with the installer. If you want to change the code, start from source.

- **Install on Windows 11 x64:** [Download the alpha installer](https://github.com/Trisoma/odessy/releases/download/v0.5.0-alpha/Odessy-v0.5.0-alpha-windows-x64-setup.exe) and follow the [desktop manual](docs/GOLDEN_PATH.md). Setup includes the runtime. The [release status](docs/current-status.md) lists the security fixes that this installer predates.
- **Run from source:** Follow [Getting started](docs/getting-started.md). The desktop starts its own local service.

Typed chat is enough for the first run. Add voice when you want to speak to the agent, or SSH when the work belongs on another machine. Expect rough edges in this alpha. [Support](docs/current-status.md) · [Limits](LIMITATION.md) · [Screenshots](docs/view/README.md)

## Build Odessy with us

I’m looking for 3–5 volunteer collaborators who want to take a focused change from reproduction through review. Pick a problem in the [roadmap](docs/ROADMAP.md) or [GitHub issues](https://github.com/Trisoma/odessy/issues). The [contribution guide](CONTRIBUTING.md) points to the files and the first test to run. Focused CI fixes and refactors that preserve behavior are welcome.

[Apache-2.0](LICENSE) · [Third-party notices](THIRD-PARTY-NOTICES.md) · [Report a vulnerability](SECURITY.md)
