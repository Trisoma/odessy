# Odessy 在线安装器

面向 Windows x64 的图形在线安装器。普通用户可使用独立 EXE 安装，无需预装
Python、Node.js 或 Git。安装器自动下载和校验锁定资源，在用户可写目录准备
运行环境、应用、本地模型及连接配置。

## 使用

1. 普通双击 `OdessySetup.exe`，选择新的独立安装目录。
2. 点击检查安装方案，核对路径、下载内容和磁盘空间，再确认安装。
3. 安装器完成环境、依赖、应用、Ollama、模型准备及真实调用验证。
4. 创建启动入口和桌面或开始菜单快捷方式。
5. 打开启动面板，选择允许操作的工作文件夹，在独立终端中输入请求。
   输入 `/exit` 退出；本次启动的模型服务随会话结束停止，已有服务保持原状。

默认安装根目录为 `%LOCALAPPDATA%\Programs\Odessy\installation`，共享下载缓存为
`%LOCALAPPDATA%\Odessy\downloads`。可以选择其他用户可写目录，不需要管理员权限。
选择工作文件夹不会跳过核心已有的权限检查或操作确认。
Windows 上自行启动的模型服务及其子进程由独立 Job 管理，退出时等待整棵进程树结束；
复用已有服务时不创建或终止该服务的 Job。

## 安装目录

| 路径 | 内容 |
| --- | --- |
| `runtime/` | 私有 Python 和 Node.js |
| `dependencies/` | 原路径创建的虚拟环境与锁定 Python 依赖 |
| `application/` | 应用文件和预编译工具服务 |
| `ollama-runtime/` | 私有 Ollama CLI 和库 |
| `models/` | 固定版本本地模型 |
| `config/odessy.env` | 此安装使用的连接配置 |
| `launcher/` | 独立启动程序及快捷方式记录 |
| `ollama-state/` | 私有服务状态和诊断日志 |
| `.installer/` | 安装检查点、维护记录和回退副本 |

不要移动已安装的 Python 或虚拟环境；Windows 虚拟环境不能直接搬移使用。

## 下载、校验与恢复

- 发布资源的 URL、版本、大小和 SHA-256 固定在 `releases/` 清单中。
  校验摘要保证资源与清单一致，不等同于发布者数字签名。
- Python 依赖资源的 v2 记录只绑定 `requires-python` 和基础 `dependencies`；
  修改测试、云端、UI 或语音可选依赖、注释和排版不会使本地依赖锁失效。
  基础依赖或 Python 要求变化时必须重新生成资源；下载清单和 wheel 摘要仍严格校验。
- 下载先进入缓存，校验通过后再使用；中断或失败后可重新检查并重试。
- 安装前按实际磁盘合并计算下载、解压、临时文件和回退空间，防止同卷重复计算。
- 写入安装检查点；恢复时重新校验已完成组件，不直接相信完成标记。
- 源码、资源或关键选项变化可能阻止旧安装任务恢复，需要重新检查。
- 原位置的完整安装记录是启动、维护和卸载的依据；不接管任意第三方目录。

## 修复与升级

从安装目录外运行新版 EXE，在设置页选择已有完整安装，打开“修复/升级此目录…”。
先关闭该安装的启动面板、终端和模型服务，再检查并确认维护。

维护检查私有运行环境、锁定依赖、Ollama、固定模型、应用及启动入口。
只准备需要修复的基础组件；健康模型继续复用。Python/Node 重建时同时重建依赖，
虚拟环境在最终路径创建。依赖检查直接读取 wheel RECORD，检查文件存在性、大小
和 SHA-256；这用于发现意外损坏，不抵御恶意改写校验记录。

新版应用先组装并验证，真实模型回复和目录工具调用通过后才切换。
原组件保留为回退副本；失败时尝试恢复。维护中断后，先用新版外部维护窗口
“恢复原版本”，再检查使用。备份变化、缺失或权限不足时停止并报告。
回退恢复维护前的状态；原来已损坏的组件不会因此自动修好。

自定义或缺失连接配置不会被默认值覆盖，会停止维护并提示处理。
快捷方式可通过“创建/补齐快捷方式”补建；同名外来链接或已修改链接保持原状。
快捷方式使用 Windows Unicode 接口创建和读取，支持系统 ANSI 代码页之外的路径。
安装记录遇到短暂文件占用时会有限重试原子替换；持续权限问题仍会报错并保留原记录。

## 清理维护备份

在修复窗口点击“查看备份占用”，核对后单独确认“清理备份”。
清理将永久放弃该次维护的回退能力。默认不删除备份。

只清理当前维护记录关联且清单未变化的 `backup-*` 副本，保留应用、模型、配置、
工作文件夹、共享缓存及其他维护文件。逐项删除清单中的文件，再移除空目录；
新增或变化的文件会停止清理。失败后需重新检查和确认，不能借重试删除新文件。
更早且已不由当前记录关联的维护目录不自动清理。文件大小合计不保证等于释放的
物理空间；清理不提升账户权限。

## 卸载

从安装目录外运行安装器的卸载界面：

```powershell
.\OdessySetup.exe --mode uninstall --install-dir D:\Apps\Odessy
```

先检查删除范围再确认；默认保留模型、用户配置和共享下载缓存。
删除模型或配置必须单独选择。用户工作文件夹不属于卸载范围。
卸载只逐项删除已经检查的内容；未列出的文件或权限问题会导致停止并保留剩余内容。
早期管理员创建、当前用户无权删除的文件不会被强行接管。

## 源码开发与构建

以下命令从仓库根目录执行。开发机需要 Windows x64、带 Tcl/Tk 的 Python，
并使用项目已有依赖与接口。输出目录应使用新的独立目录；示例路径可自行替换。

准备私有构建运行环境和预编译工具包：

```powershell
python -m installer.runtime --runtime-dir D:\OdessyBuild\runtime --prepare
python -m installer.toolbundle --runtime-dir D:\OdessyBuild\runtime --output-dir D:\OdessyBuild\tool-bundle --build
```

启动源码向导或构建独立 EXE：

```powershell
python -m installer.wizard --tool-bundle D:\OdessyBuild\tool-bundle
python -m installer.build_exe --tool-bundle D:\OdessyBuild\tool-bundle --output-dir D:\OdessyBuild\setup --build
```

构建器在隔离环境中使用 `releases/windows-x64-bootstrap/build-requirements.txt`
固定的构建依赖，输出 `OdessySetup.exe` 和 `build-report.json`。
最终用户安装不运行 npm 或 TypeScript 构建。

命令行工作流默认仅预览；添加 `--run` 才开始安装：

```powershell
python -m installer.workflow --install-dir D:\Apps\Odessy --tool-bundle D:\OdessyBuild\tool-bundle
python -m installer.workflow --install-dir D:\Apps\Odessy --tool-bundle D:\OdessyBuild\tool-bundle --run
python -m installer.workflow --install-dir D:\Apps\Odessy --status
python -m installer.workflow --install-dir D:\Apps\Odessy --resume
```

`installer.component_repair`、`installer.backup_cleanup` 和 `installer.uninstall`
提供维护、备份清理和卸载的命令行入口。先用 `--help` 查看参数；会删除文件的操作
需要先预览，再显式提供该方案的 `plan_sha256` 确认。

运行安装器测试：

```powershell
python -m unittest discover -s tests -p 'test_installer*.py' -q
```

提交源码时保留安装器、测试程序、必要资源清单和说明；不提交 EXE、依赖包、模型、
临时安装目录、缓存、测试输出或构建日志。

## 当前范围

当前实现为 Windows x64、本机部署和固定本地模型的安装及维护链路。
语音依赖自动准备、模型迁移、云账户配置、分布式部署及核心工作流的极简 UI
尚未包含。启动面板仍通过终端输入请求和完成确认。

运行时检查不保证发现任意未探测的文件损坏，也不保证所有硬件的模型性能。
干净 Windows 机器验收和正式发布尚待完成，`installation_ready` 保持为 false。
