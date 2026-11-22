# Odessy

[English](README.md) | **简体中文** | [Español](README.es.md)

**开发者预览 · v0.5.0-alpha**

> 说明：英文文档为权威版本（canonical），本页为简体中文翻译，仅供参照；界面截图显示英文界面，应用本身尚未本地化。

你好，这是 Odessy，不是 Odyssey！⛵

> 想知道项目是怎么开始的吗？[读一读 Odessy 的故事](https://github.com/Trisoma/odessy/blob/develop/Story.md)。

我在做 Odessy，因为我想开口让电脑帮我做事，就像 JARVIS 或 FRIDAY。现在的 Odessy 是一个实验性的 Windows 桌面助手。它可以使用本地或云端模型，在你的电脑上执行获批的操作，也可以向已连接的电脑发送范围明确的命令。

模型提出操作，Odessy 的服务检查权限后才执行。[工作原理](docs/zh-CN/ARCHITECTURE.md)

## 试用 Odessy

如果你想在 Windows 上试用 Odessy，从安装包开始。如果你想改代码，从源码启动。

- **在 Windows 11 x64 上安装：** [下载 alpha 安装包](https://github.com/Trisoma/odessy/releases/download/v0.5.0-alpha/Odessy-v0.5.0-alpha-windows-x64-setup.exe)，按[桌面手册](docs/zh-CN/GOLDEN_PATH.md)操作。安装包自带运行时。[发布状态](docs/zh-CN/current-status.md)列出了此安装包发布前未纳入的安全修复。
- **从源码启动：** 参照[入门指南](docs/zh-CN/getting-started.md)。桌面端会自己启动本地服务。

第一次用键盘就够了。想开口交流时再配置语音，需要在另一台电脑上做事时再连接 SSH。这个 alpha 版本还有不少粗糙之处。[支持范围](docs/zh-CN/current-status.md) · [限制](docs/zh-CN/LIMITATION.md) · [截图](docs/view/README.md)

## 一起做 Odessy

我在找 3–5 位志愿协作者，把一个具体问题从复现做到评审。可以从[路线图](docs/ROADMAP.md)或 [GitHub issues](https://github.com/Trisoma/odessy/issues)里选题。[贡献指南](docs/zh-CN/CONTRIBUTING.md)列出了入口文件和第一条测试命令。欢迎小范围的 CI 修复和保持行为不变的重构。

[Apache-2.0](LICENSE) · [第三方声明](THIRD-PARTY-NOTICES.md) · [报告安全漏洞](SECURITY.md)
