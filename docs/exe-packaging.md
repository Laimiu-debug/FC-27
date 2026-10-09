# Windows EXE 发行包

`dist/FC27Manager/FC27Manager.exe` 是 Windows x64 单文件桌面程序，当前版本 0.2.0。双击后在独立窗口显示中文管理界面，运行无需安装 Python，无需开启命令行或手动输入浏览器地址。发行目录另附使用说明、第三方许可和含 EXE 尺寸、源提交及 SHA-256 的 `release-manifest.json`。首次配置、任务历史与诊断说明见 [0.2.0 改进](manager-improvements.md)。

## 使用

将 EXE 保留在当前项目的 `dist/FC27Manager/` 下，即可自动连接现有 `local/mod-manager/`。根目录 `启动管理器.cmd` 也会优先启动此 EXE；没有构建产物时才回退到 Python 浏览器版。

若将 EXE 移到项目外，启动时连接已有目录或创建新离线工作区；随后通过设置向导提供本机研究输入。Windows 只在用户注册表保存上次工作区位置，不迁移研究文件。也可显式指定：

```powershell
.\FC27Manager.exe --workspace 'X:\Projects\FC27' --root 'local/mod-manager'
```

研究项目与游戏安装目录是两个不同的目录。EXE 内含程序、Python 运行时、前端、自有研究方案和配置示例，以及 WebView2 SDK 适配组件；游戏文件、SDK 研究样本、Oodle、候选、配置和备份继续从外部工作区读取，不打入发行包。复制 EXE 不会复制或迁移这些数据。

系统需已有 Microsoft Edge WebView2 Runtime 与 .NET Framework 4.6.2 或更新版；本机已有并通过实际窗口检查。另一台电脑需要自行安装 [微软 WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。发行包没有捆绑整个浏览器运行时，不能将本机检查称为无依赖、跨机器兼容性验证。

窗口关闭或点击“退出管理器”时，停止接受新任务，等待正在执行的管理任务结束，再关闭本机服务。每个管理目录只允许一个桌面实例；其他命令行管理操作仍由原有文件锁保护。不要强制终止正在执行文件事务的进程。

服务仅监听自动选择的 `127.0.0.1` 端口，前端继续使用原来的 Host、Origin 与会话令牌检查。`/api/exit` 仅由桌面宿主启用，使用同一请求验证。当前状态、错误日志和浏览器缓存保存在项目 `local/desktop-manager/`，状态文件不保存界面会话令牌。临时解包目录只存放程序资源，不能作为研究工作区。

## 从源码构建

在 Windows x64 上使用 Python 3.13，创建项目内隔离构建环境：

```powershell
python -m venv local/packaging/venv
.\local\packaging\venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r resources/packaging/requirements-exe.txt
.\local\packaging\venv\Scripts\python.exe scripts/build_fc27_exe.py
```

版本锁定为 PyInstaller 6.22.3、pywebview 6.2.1 及锁定文件中的依赖。`resources/packaging/fc27-manager.spec` 指定无控制台、普通权限、单文件封装。构建不申请管理员权限，不使用 UPX；每次输出必须是新目录：

```powershell
.\local\packaging\venv\Scripts\python.exe scripts/build_fc27_exe.py --output dist/FC27Manager-next
```

构建器清理子进程的 DLL 搜索路径，收集许可证，核对随 pywebview 提供的三个 Windows x64 WebView2 适配 DLL 与官方 NuGet 1.0.3856.49 包完全一致。`proxy_tools` 的 PyPI 包缺少完整许可证文件，采用作者仓库 [LICENSE.txt](https://github.com/jtushman/proxy_tools/blob/master/LICENSE.txt) 的原文副本。

创建 EXE 前逐个核对打包来源，只允许项目源码、界面资源、两份自有公开模板、生成的许可、指定隔离环境、Python 安装的运行时和构建器生成的标准库归档。构建审计保存在 Git 忽略的 `build/fc27-exe-*/bundle-audit.json`，可能含本机工具路径，不进入发行包。`local/` 中的研究素材、个人配置与候选均不参与收集。构建器另生成发行 ZIP 与 SHA256 文件；版本唯一来源为 `src/fc27_version.py`。

## 检查

```powershell
python -m unittest discover -s tests -v
.\dist\FC27Manager\FC27Manager.exe --smoke-test
```

桌面检查实际创建 WebView2 窗口，等待内嵌页面读取本机状态，核对页面标题、候选卡与退出控件，然后自动退出；结果写入 `local/desktop-manager/` 的会话 JSON。`--browser` 可临时用默认浏览器检查同一封装后端，退出按钮仍会停止该服务。

本机 0.1.0 的 EXE 为 15,612,215 字节。已在项目外的当前目录、PATH 不含 Python 的环境下完成独立窗口检查；封装服务通过角色候选预检，核对 327,337 个 Bundle 位置。随后从界面构建并登记 `exe-role-v1`：1 份资产、7 项修改、21 个变更字节，完整流水线完成至 `verify-loader-stage`。另在预检运行时点击退出，页面显示“当前任务完成后退出”，随后桌面进程与本机监听结束。79 份原版元数据与对应原版清单的 SHA-256 一致；未执行原版 CAS 全文件散列或比赛验证。本机证据保存在 Git 忽略的 `local/research/2026-10-09/notes/`。

EXE 只封装已有离线研究管理能力，不增加游戏安装、启动或绕过保护的能力。封装签名接受性、游戏加载与比赛效果继续保持未验证。
