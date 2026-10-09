# FC27 离线工具链原型

当前实现包括 DbObject 读写、资产导出、RIFF 检查、静态 SDK 名称提取、本机共享类型布局适配、字段移植实验和按计划批量定点编译。336 个同名参考资产均可读取受支持字段，但模式、数组列含义及比赛效果仍未验证；不能导出可加载的玩法模组。用户已要求停止实机验证，当前继续离线开发。

## 已完成

- 读取 `00 D1 CE 01` 封装中的 DbObject 节点，检查类型、长度、边界与结束符。
- 保留对象字段顺序及同名字段，保留浮点数原始位和不透明 Blob。
- 将节点树重新编译为二进制；字段长度变化时重新计算容器长度。
- 在内存中验证原版文件读取、重新编码后是否逐字节相同。
- CLI 只读读取安装目录，报告只能新建在本项目 `local/` 下，禁止覆盖现有文件或向游戏目录写报告。
- 解析本机 FC27 的 36 字节 CAS Bundle 头和 64 位 CAS 标识，按安装目录的 `persistentIndex` 定位基础或补丁资源。
- 读取 Standard BinaryBundle 的 EBX 清单、原始大小和 SHA1；补丁 TOC 的同名资产优先，遇到同层不同内容则拒绝猜测。
- 校验目标压缩数据的 SHA1，按声明长度解压，导出到项目内新目录。
- 重建 RIFF EBX 封装并对照原始字节；检查 EFIX 引用范围、EBXX 数组表及 Float32 数组长度。不透明块和填充原样保留。

解析代码使用 Python 标准库，建议 Python 3.10 或以上。实际开发验证环境为 Python 3.13。扫描索引不需要本地解压库；当前资产使用 Oodle Leviathan，导出需显式提供已校验的库。

## 运行

下面的游戏目录只是占位示例，请使用实际安装路径。命令不启动游戏，不运行编辑器，不生成安装数据。

```powershell
python scripts/fc27_offline.py --game-root 'X:\Games\EA SPORTS FC 27'
python scripts/fc27_offline.py --game-root 'X:\Games\EA SPORTS FC 27' --report 'local\fc27-roundtrip.json'
python -m unittest discover -s tests -v
```

CLI 固定检查 `Data/layout.toc`、`Patch/layout.toc`、`Data/initfs_Win32`、`Patch/initfs_Win32`。退出码 0 表示这四份文件的未修改读写验证通过；2 表示至少一份未通过或格式不支持；1 表示路径或文件操作错误。

扫描或导出玩法、角色及特性参数：

```powershell
python scripts/fc27_export.py --game-root 'X:\Games\EA SPORTS FC 27' --report 'local\fc27-index.json'
python scripts/fc27_export.py --game-root 'X:\Games\EA SPORTS FC 27' --codec 'local\research\2026-10-08\tools\codec\oo2core_9_win64.dll' --output 'local\exports\fc27-attrib-01'
```

第二条命令中的库路径指向本项目已提取的研究依赖。不是游戏安装目录中的 DLL。工具只接受 SHA256 为 `ca9015662ac0a9a29be4ce5f0d6eacd239ebc893619f39e9972559d73bcf2c0a` 的库，拒绝从游戏目录加载库；不会启动编辑器、管理器或游戏。这一 DLL 来自已下载并检查 ZIP CRC 的 FMT 公开发行包，仅用于本机解压，未加入源码或分发包。

`--name` 可选择一个完整资源名，例如 `fifa/attribulator/gameplay/groups/gp_cpuai/gp_cpuai_cpuaipredictionpoints_runtime`。扫描固定读取基础及补丁的 `Win32/fc/fcgame/fcgame.toc`，只选择 `fifa/attribulator/` 下的 EBX；这不代表已经覆盖游戏的所有资源包。

导出目录必须不存在且位于项目 `local/` 内，工具在全部解压和验证通过后创建目录。每个导出 EBX 和 `manifest.json` 都使用新建模式。单次导出原始数据上限为 64 MiB；单份元数据或资产上限为 32 MiB。退出码 0 表示本次扫描或导出成功，1 表示错误，不表示游戏加载成功。

## 2026-10-08 验证结果

针对本机 Steam BuildID `25726320`、可执行文件产品版本 `27.0.9253130.0`，四份文件读取后重新编码均逐字节相同，SHA256 相同。这些编号不代表已确认的官方 Title Update 名称，也不保证其他构建兼容。

基础及补丁 `layout.toc` 分别解析出 5,252 和 5,661 个节点，均列出 93 个超级包。两份实际索引都包含 23 个重复名称的对象字段，因此原型使用有序节点保存，避免普通字典覆盖同名字段。通用 DbObject 检查保留 `initfs_Win32` 的不透明 Blob；独立 initfs 工具已验证 AES 解码和内外层逐字节重编码，仅导出共享类型描述，不保存其他内部文件或完整明文。

145 项测试通过，覆盖 DbObject、CAS、RIFF、CLR 属性、类型身份、集合元素类型、当前共享布局、空数组、重复身份拒绝、基线散列、候选修改范围、initfs 导出范围、定点编辑、反向恢复、批量构建、Frosty v6 容器、索引重定位、CAS 注册表、磁盘打包校验、跨版本研究选择、曲线几何、六模块方案、加载副本/回退、流水线及管理器事务边界。6 份本机元数据的 SHA256 在原版启动后及离线打包阶段复查，与开始研究时相同；此检查不代表对整个安装目录逐文件校验。

两份 `fcgame.toc` 的 30 个 Bundle 共读取 201,679 条 EBX 记录。在 `fifa/attribulator/` 下导出 1,237 个不同名称的当前资产：`gameplay` 1,060 个、`prematch` 105 个、`perks` 71 个，以及一个资产数据库入口。全部目标压缩数据的 SHA1 与索引一致，全部解压后的 RIFF 封装重新编码后逐字节相同，总原始大小为 4,547,450 字节。

基础和补丁 TOC 都包含这些名称，其中 5 个资产的内容散列不同；导出使用补丁 TOC 引用的版本。补丁 TOC 可引用基础 CAS，不能简单地只扫描 Patch 文件夹中的 CAS。

与 Anth FC26 样本中的 462 个资源路径对照，336 个在这一扫描范围内同名匹配。其余 126 个仍需检查更名、结构或索引范围，不能直接认定被删除。本机共享类型表包含 6,502 个类型、46,520 个字段描述；取得了研究范围所需的 366 个类型布局。此前 111 项签名变化和空数组读取问题已解决，336 项均可读取受支持字段；其中 8 项前代参考资产带有明确记录的数组元数据异常，只在完整 SHA256 已核对的样本诊断中允许读取。第一个离线候选仍只移植三个预测点字段的 15 个值，改变 35 字节。详细依据和限制见 [字段适配实验](field-adaptation.md)。

DbObject 原型完整保留封装头；RIFF 组件保留各块的原始载荷。候选组件只改已经检查的固定长度数值位置，并生成无压缩 CAS 块；未实现字段重排、引用修正、游戏封装签名或加载处理。当前组件仍不是完整的 EBX 对象编译器；离线字节和结构验证不证明游戏兼容性。

定点编译工具已用两份实际 FC27 资产验证批量构建：预测点的 15 个数组值，以及盯防反应曲线的一项 Y 值，合计改变 16 个值、38 字节。原始副本、候选和独立 CAS 块已保存；每项反向恢复均与基线逐字节相同，独立磁盘校验命令通过。该实验用于验证多资产编辑链路，未将参考值作为正式 AI 优化方案。详见 [定点编译工具](fixed-build.md)。

已将上述两项资产写入受限 Frosty v6 实验容器，并生成两份 Bundle 索引候选、两份 TOC 候选、两份 layout CAS 注册表候选和两个新 CAS 副本。从绑定原始基线重新编译，与磁盘上的 11 个产物逐字节比对通过。这里的完整容器是公开 Frosty v6 格式，不是 Anth 的 FETM v1；目标 profile 仍是未验证的研究标签，也没有执行加载器。详见 [模组打包说明](mod-packaging.md)。

整场比赛研究已扩展到六组、16 份资产、69 个明确根字段。147 项数值修改合计改变 298 字节，通过干跑恢复、实际批量构建、容器与全部目标 Bundle 引用编译、11 份磁盘产物重建比对。还用攻守转换模块独立跑通单命令流水线。详见 [整场比赛研究方案](whole-match-study.md)；参数效果与加载仍未验证。

新增 [项目内加载副本与原版 CAS 回退审查](loader-adaptation.md)，完整实验的 327,337 个 fcgame Bundle 位置及 32 次选定引用核对通过，并生成一个数值、一个字节的最小诊断候选。封装签名接受性和实际引擎加载仍未解决；不生成安装目录链接、不启动游戏。

## 已取得的研究依赖

- FIFA Editor Tool v2.1.2 官方公开 ZIP：133,235,310 字节，ZIP CRC 检查通过。包内 SDK 到 FC26，没有 FC27SDK.dll；没有启动编辑器。
- FIFA Mod Manager v2.1.2 官方公开 ZIP：63,742,919 字节，ZIP CRC 检查通过。未运行管理器或部署模组。静态读取其中 `Modding.dll` 时被 Windows 安全软件拦截，已停止使用该组件；CRC 和散列只验证下载一致性，不能证明文件安全，也未确认是否误报。
- NuGet 的 FMT Ebx、FileTools、ProfileSystem、InitFSSupport、Compilers、Core、ServicesManagers、Resources 公开包：已下载，部分用 ILSpy 静态研究接口。没有执行这些应用组件；本项目解析代码没有引用或复制这些库。
- 用本地安装的 `dnfile` 静态读取 FC26SDK.dll 元数据，保存了部分玩法类字段。没有加载或执行 SDK DLL。
- 在 FC27.exe 的原始磁盘字节中找到部分相同字段名；这只是适配线索，不证明其类型布局或运行时可用性。

下载文件、完整 SHA256、字段研究记录和本机报告保留在被忽略的 `local/research/2026-10-08/` 下，未将第三方二进制或游戏资产加入源码。

## 后续需要实现的部分

1. 继续解决未知字段名称和未支持的字段种类。布局来自 FC27 共享描述，前代 SDK 只在 GUID 与名称哈希对应时提供名称；不能将读取成功等同于完整语义适配。
2. 确认难度、模式及数组列对应关系；取得前代原版基线后，才能把模组修改量和跨版本差异分开。
3. 现有曲线点和数值的固定长度编辑已实现；继续补足点数变化、引用重建及完整对象编译，并研究参数关联影响。
4. 已实现受限 Frosty v6 容器、资源标识与当前索引候选；继续确认 FC27 加载工具的 profile 映射、封装处理与导入兼容性。FETM v1 容器写入尚未实现。
5. 形成明确标注未实测的候选玩法包。实机验证已按用户要求停止，今后仅在用户重新要求时开展。

## 格式研究来源

DbObject、BinaryBundle、CAS 压缩头和 RIFF EBX 结构参考了 [Frosty 官方开源实现](https://github.com/FrostyToolsuite/FrostyToolsuite/tree/master/FrostySdk)。TOC 的前代结构另参考 [FMT 历史公开实现](https://github.com/na632/FrostbiteModdingTool)。本项目依据格式资料以 Python 实现范围受限的组件，没有复制游戏代码或引入这些工具的应用组件；FC27 的差异以本机数据校验。研究快照及上游许可保留在本机目录。Oodle 库是上述显式选择的独立解压依赖。

下载来源：[FIFA Editor Tool 官方下载页](https://www.fifaeditortool.com/download)、[FMT.Ebx 官方 NuGet 包](https://www.nuget.org/packages/FMT.Ebx/2026.12.1)、[FMT.ProfileSystem 官方 NuGet 包](https://www.nuget.org/packages/FMT.ProfileSystem/2026.14.1)、[FMT.FileTools 官方 NuGet 包](https://www.nuget.org/packages/FMT.FileTools/2026.11.0)。
