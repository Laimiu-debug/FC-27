# FC27 字段适配与首个离线实验

已生成一个预测点参数的离线移植实验，用于验证真实玩法资产的修改链路。它包含候选 EBX、独立 CAS 压缩块和逐字段差异报告，尚不能安装到游戏，也没有比赛效果结论。

## 数据来源与检查依据

- FC27 基线：本机 Steam BuildID `25726320`，产品版本 `27.0.9253130.0`，此前已导出并逐项验证的资产。
- 前代参考：已下载的公开 `Anth FC26 FREE V5 REGULAR` 主文件。SHA256 为 `2ee9507f42c272beaedf1bfa5a4fb0ef152269890cebf160742e58d494dfda37`。
- 当前布局：从本机 `Data/initfs_Win32` 导出的 `SharedTypeDescriptors.ebx`，868,496 字节，SHA256 为 `25566c8b8f2e997d16d561ee1f1187491194be39c6b8cc7522b2766ddd0bd2b3`。包含 6,502 个类型身份与描述、46,520 个字段描述；本次补丁 initfs 没有额外共享类型文件。
- 字段名称参考：FIFA Editor Tool v2.1.2 包内的 FC26SDK.dll，SHA256 为 `0462a9801210599373e2b881771638ddcf1c1873a13a6157000676948fbe065b`。

SDK 只作为磁盘数据读取，不加载或执行 DLL。自有 CLR 读取组件检查 PE 到元数据的映射、表索引、属性 Blob 和集合元素类型。FC27 的字段偏移、大小、对齐、标志与类型签名来自本机共享类型表；旧 SDK 仅在 GUID、类型名称哈希和字段名称哈希对应时提供名称。未知名称保留为 `Field_<hash>`，不猜测含义。

复用 SDK 描述时，同时检查 EBX 的完整 GUID/签名、实际实例位置、EFIX 相对指针、EBXX 数组位置与数量。FloatCurve 的结构数组通过 CLR 集合元素类型与 EFIX 的类型身份核对；前代样本的 EBXX `type_ref` 可超出 EFIX 类型数，不能直接当作 EFIX 本地索引。

共享类型中的字段引用按其所属描述块的类型表索引解析，不能当作 EFIX 本地索引。重复 GUID/签名、越界字段或引用会拒绝解析。当前读取研究范围所需的 366 个 FC27 类型布局，尚未生成全游戏的具名 SDK，也没有证明模式或运行时含义。

## 当前对照范围

提取前代样本的 462 个参数资产，总原始大小为 1,445,614 字节。每项压缩载荷的 SHA1 与样本记录一致，每项 EBX 封装读写逐字节相同。

其中 336 个与已导出的 FC27 资产同名：

| 状态 | 数量 | 含义 |
| --- | ---: | --- |
| 可读取受支持字段 | 336 | 10,312 个受支持字段，其中 9,885 个有核对名称；累计读取 115,747 个数值 |
| 其中根类型签名已变化 | 111 | 使用各版本的实际布局分别读取，依据 GUID 与名称哈希比较；不套用旧偏移 |
| 参考资产带元数据诊断警告 | 8 | 前代曲线数组标志异常，在已核对完整 SHA256 的固定样本中记录警告；通用读取仍拒绝 |
| 无法读取受支持字段的资产 | 0 | 此前 112 项资产读取障碍已解决；不表示所有字段都已支持 |

受支持资产仍包含 344 项未支持字段记录，以及 480 项未知字段名称记录；其中 427 项未知名称字段已经能够读取受支持数值，其余未知名称仍在未支持记录中。上述数量按资产中的字段出现次数统计，不是去重后的字段种类。不同的曲线形状单独保留在报告中。不能把资产读取成功理解为全部字段或语义均已适配。

新增的空数组读取要求 EFIX 指针指向已核对的保留零区域，同时核对偏移、高位、数量和区域边界；不会把任意零计数位置当成合法数组。前代的 8 项诊断资产合计记录 9 项曲线警告，仍核对集合元素身份、大小、指针和边界，不修复其原始标志，也不把异常机制用于 FC27 候选写入。

对照对象是“FC26 模组修改后的数据”与“FC27 当前原版数据”。差异同时包含游戏版本差异；由于尚无 FC26 原版基线，不能把所有差异归因于 Anth 的修改。

当前 FETM 读取器仅支持上述已校验 SHA256 的公开样本。样本资源区位置依据该文件的静态研究配置，并逐项验证载荷散列；未知文件直接拒绝。这不是通用 FETM 编辑器或打包器。

## 预测点实验

资源：`fifa/attribulator/gameplay/groups/gp_cpuai/gp_cpuai_cpuaipredictionpoints_runtime`。

根类型 GUID 为 `cc9b596b-9b2a-9a32-3884-c17918a3a010`，签名为 `636051d6`。FC27 的共享描述与前代 SDK 分别给出的字段位置、实际数组指针、三个数组标识及元素数量均对应。该候选仍要求根类型完整 GUID/签名一致。

| 字段 | FC27 当前值 | Anth 参考值 |
| --- | --- | --- |
| PredictionPointMinMovementDelta | 0.3, 0.3, 0.3, 0.3, 0.5 | 0.8, 0.9, 1.0, 1.1, 1.4 |
| PredictionPointMaxMovementDistance | 100, 100, 100, 100, 70 | 45, 42, 38, 34, 30 |
| PredictionPointMaxSpaceDistance | 70, 50, 50, 50, 50 | 320, 260, 240, 220, 220 |

表中小数用于阅读，移植使用参考文件的 Float32 原始位。五列的难度、模式或其他用途尚未确认；仅按原有顺序制作技术实验，不据此声称球员更聪明。

候选只替换这 15 个值，改变 35 个字节，文件长度保持 486 字节。原 FC27 的 EFIX、EBXX、不透明块及填充保持不变。候选重新读取后具名值逐项吻合，RIFF 封装读写一致；生成的 494 字节无压缩 CAS 块解码后与候选 EBX 相同。

`.casblocks` 是独立资源压缩块，不是完整 CAS 归档；当前没有生成修改后的 Bundle 索引、模组容器、签名或安装数据。前代角色、射门与防守参数尚未批量移植。

## 重现方法

先使用导出工具建立已校验的 FC27 基线；以下项目内路径对应本机研究资料，游戏路径仍是占位示例。

```powershell
python scripts/fc27_initfs.py --game-root 'X:\Games\EA SPORTS FC 27' --key-file 'local\research\2026-10-08\sources\FrostbiteModdingTool-legacy\Libraries\FrostySdk\FrostbiteKeys\the.key' --output 'local\experiments\shared-types-01'
python scripts/fc27_compare.py --game-root 'X:\Games\EA SPORTS FC 27' --fc27-export 'local\research\2026-10-08\extracted\fc27-attrib-v3' --sample 'local\research\2026-10-08\extracted\anth-files\main.fifamod' --sdk 'local\research\2026-10-08\extracted\fet-sdk\FC26SDK.dll' --codec 'local\research\2026-10-08\tools\codec\oo2core_9_win64.dll' --shared-types 'local\experiments\shared-types-01\Data\SharedTypeDescriptors.ebx' --output 'local\experiments\predictionpoints-01'
python -m unittest discover -s tests -v
```

initfs 解码使用已取得公开源码中的格式依赖，仅通过 Windows PowerShell/.NET AES 处理输入，禁止读取账户凭据。格式依赖保留在 Git 忽略的研究目录；不将其内容写入输出或源码。工具只导出允许的共享类型文件和散列清单，完整明文不落盘。输出必须是项目 `local/` 下的新目录。

输出只能创建在项目 `local/` 下的新目录，并拒绝覆盖现有内容或写入游戏目录。工具先完成全部源数据校验和候选验证，再创建结果。输出包括：

- `reference-assets/`：已校验的前代参数载荷，仅本机研究使用。
- `original.ebx`、`candidate.ebx`、`candidate.casblocks`：基线副本与技术实验。
- `candidate-report.json`：每项值的原始位、修改位置、散列及未实测状态。
- `field-comparison.json`：各资产的支持、拒绝、未支持字段和形状差异。
- `sdk-matched-schemas.json`：本次使用的 SDK 静态描述与来源散列。
- `fc27-shared-schemas.json`：本次使用的 FC27 实际布局、已核对名称和未知名称标记。
- `summary.json`：本次范围和验证结果。

字段对照结果在 `local/research/2026-10-08/candidates/predictionpoints-v9/`，候选字节与 v8 相同。当前工具链共 72 项测试通过，新增的多资产定点编辑与恢复验证见 [定点编译工具](fixed-build.md)。

脚本中的 `game_started_by_tool: false` 表示该离线脚本不启动游戏；独立的原版实机检查已在授权后开展，见 [实机测试记录](live-testing.md)。没有向安装目录写入候选，没有验证候选加载或比赛效果。

## 剩余工作

继续解决未知字段名称与未支持字段；确认参数列和游戏模式的关系；补足曲线点数变化与完整资源编译；确定 FC27 加载工具要求的容器、标识、散列及签名。现有曲线点的固定长度编辑已实现。实机验证已按用户要求停止，当前继续离线开发。

2026-10-08 重新核对，[FIFA Editing Toolsuite 官方公开下载页](https://www.fifaeditortool.com/download)仍列 v2.1.2，已检查该包没有 FC27SDK.dll；[FMT 官方支持表](https://github.com/FMTDev/FMT.Releases#supported-games)将 FC27 PC 标为 PRO 版只读支持。这是本次取得的公开资料范围，不代表所有其他版本都不存在。当前不把这些工具描述成已具备 FC27 编译或部署能力。
