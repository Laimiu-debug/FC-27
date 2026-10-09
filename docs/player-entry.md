# 普通用户入口与版本绑定预设

0.3.0 修正交付范围：默认显示游戏识别、内置方案和当前启用状态。SDK、前代参考包、导出目录及编译参数保留在折叠的开发者工具中，普通用户无需填写这些项目。独立下载的 EXE 自动在 Windows LocalAppData 的 FC27CareerLab/workspace 创建外部工作区；原有工作区仍优先复用，临时解包目录不保存研究数据。

**当前仍不能启用玩法包。** 主页面明确显示“暂不可启用 · 加载适配未完成”，后端没有安装、启用、启动游戏或真实游戏还原接口。离线准备完成不改变这项状态。普通用户无需执行离线准备，也无需协助实机测试。

## 已补齐的免配置编译

公开的 `resources/whole-match-preset.json` 为 42,160 字节的数值编辑描述：16 份资产、147 项编辑，分为六组。它不包含原版或候选 EBX、CAS、TOC、共享类型表、SDK、前代参考包或账号数据。来源为之前完成审查、编译和逐字节恢复的整场方案，仍是研究假设，不能称为已验证 AI 优化。

程序首先核对本机四份 layout/fcgame 索引及两层 initfs 类型来源的完整 SHA256，重新扫描实际 Bundle 并从原版 CAS 读取资产；逐项核对压缩 SHA1、尺寸、解压后完整 SHA256、RIFF、根类型完整 GUID/签名和修改前数值位。只有与已审查资产逐字节相同的版本才能使用记录的偏移。预设不是跨版本字段适配器，游戏更新后直接拒绝，需要开发者重新审查并发布新的预设。

编辑仅限 EBXD 数值区域；拒绝重叠、非有限数值和越界，核对完整候选 SHA256、RIFF、不透明引用表不变，以及逐字节反向恢复。随后复用现有索引编译、全部已覆盖 Bundle 引用核对、实验容器、加载副本和独立重建验证。报告明确记录 `runtime_schema_adaptation=false`：SDK/共享类型的散列是先前审查来源，本次没有重新加载或执行它们。

Oodle 解压库自动复用外部 local 中已允许 SHA256 的研究依赖。缺少时，开发者“离线准备”操作从 [FMT 官方固定发行包](https://github.com/FMTDev/FMT.Releases/releases/tag/FMT-26.10.9654.14105) 下载约 249 MiB ZIP，核对固定大小、完整 ZIP SHA256、唯一目标成员和 DLL SHA256，只提取解压库至外部 local，不安装或执行 FMT/Mod Manager，不读取游戏内 DLL，不执行未知散列库。发行 EXE 不包含 Oodle 或整个第三方工具。下载失败保留独立 `.part` 文件，不将其作为可执行依赖，不覆盖错误的现有依赖。

开发命令如下，普通用户无需运行：

```powershell
python scripts/fc27_preset.py prepare --game-root 'X:\Games\EA SPORTS FC 27' --module all
python scripts/fc27_preset.py prepare --game-root 'X:\Games\EA SPORTS FC 27' --module role_execution
```

输出只在工作区 `local/preset-builds/prepared-<随机标识>/`，包含原版选定资产副本、候选、编译计划、实验索引、ModData 副本和记录。完成记录同时含 `prepared=true`、`installed=false`、`enabled=false`，以及全部未验证能力标志。历史记录只显示之前的准备结果，不作为当前引擎读取证明。

## 本机离线证据

2026-10-09，免 SDK/参考包配置的全部六组准备流程完整通过：16 份资产、147 项数值、298 个变更字节，16 份候选均与之前的编译结果逐字节一致。包和加载副本均独立重建校验通过；复核之前记录的 79 份原版元数据散列，全部未变。检查范围不是全部游戏文件或所有原版 CAS 的完整散列。另通过页面操作完成角色执行单模块准备。

## 真正的加载问题

2026-10-09 再查 [FMT 官方支持表](https://github.com/FMTDev/FMT.Releases#supported-games)，FC27 PC 仍为 50%、只读、PRO 限定，未提供可核实的当前版本编译/加载映射。[FIFA Editing Toolsuite 下载页](https://www.fifaeditortool.com/download) 仍显示 v2.1.2，通用介绍不能证明 FC27 已兼容。搜索中仅有 README、缺少实际源码并要求执行外部脚本或关闭安全保护的所谓 FC27 管理器，没有作为依赖下载或运行。

进一步静态查看已下载的前代公开 `FrostySdk/Utilities.cs`：`CreateTOCSignature` 使用 HMACSHA1 和 RSA PKCS1 签名接口，并要求 256 字节结果。因此前代写入器的签名更新不是普通 CRC 重算；这仍不能确定 FC27 使用相同密钥、验证方式或当前签名接受条件。本轮没有复制、提取、使用或分发签名私钥，没有修改游戏验证逻辑。新版 profile 名称、实际加载入口、原版 CAS 回退、独立 Chunk/其他超级包范围，以及引擎对修改封装的接受性仍需可靠依据，见 [加载适配](loader-adaptation.md)。

这个缺口没有被界面改动或免配置编译解决。在当前禁止实机验证的要求下，也无法取得“游戏确实读取候选”或“比赛 AI 改善”的实机证据。开放启用前必须先完成实际加载适配、版本核对、安装备份与可恢复验证，再独立获取允许范围内的实际加载证据；不能将保存签名区、离线解析通过或历史准备记录当成已经启用。
