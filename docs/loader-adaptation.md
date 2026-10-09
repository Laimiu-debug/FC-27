# FC27 编译与离线加载适配

已补齐项目内 ModData 副本、显式原版 CAS 回退清单、离线读取器和独立重建验证。工具会核对当前安装数据的实际目录、CAS 注册表、引用范围与候选载荷，但不会把这个目录交给游戏。`offline_mount_verified` 仅表示自有离线读取器的检查通过，`loadable_mod` 和 `loader_compatibility_verified` 仍为 false。

## 当前实现

`scripts/fc27_stage_loader.py` 从已验证的定点构建和实验包开始，重新编译核对打包产物，再生成以下目录：

```text
loader-stage/
├── ModData/
│   ├── Data/           # 本次修改的 layout/TOC、新 CAS，以及原版非 CAS 元数据副本
│   └── Patch/
└── loader-stage-report.json
```

只扫描安装目录的 Data/Patch，不读取账号目录、存档或启动器配置。原版非 CAS 文件按原字节复制，包括其他 TOC、initfs、locale 和 chunkmanifest；其他 TOC 不在本轮解析范围。原版大型 CAS 不复制，报告为每个回退文件记录相对路径、尺寸与修改时间。回退清单不是实际文件链接，也不代表游戏会自动从安装目录回退读取。

`src/fc27_mount.py` 按副本中的两层 layout 提取安装包映射，并合并 CAS 注册标识。补丁 TOC 引用基础 CAS 时，按标识中的实际层选择 Data 路径。副本优先；没有副本时只接受明确列入清单的原版 CAS。它检查基础和补丁 fcgame.toc 的所有 Bundle 位置是否存在、是否超过文件末尾，读取 Bundle 元数据，并检查每个选定 EBX 在全部已编译引用中的 SHA1、SHA256、原始尺寸与来源。

验证范围必须区分：未选载荷只检查文件存在性、尺寸/时间绑定与位置边界，没有计算所有原版 CAS 的完整散列；独立 TOC Chunk 表和其他超级包没有解析。候选本身、复制的元数据、原始四份索引和选定载荷则按完整字节或散列核对。

新副本只能在项目 local 内创建，拒绝覆盖旧目录或写入游戏目录；最多 10,000 个原版清单项、160 MiB 副本数据，单份元数据仍受 32 MiB 上限限制。拒绝大小写冲突、非规范路径、符号链接和目录联接。版本、文件清单、报告或磁盘内容改变时停止。没有游戏安装或卸载操作，报告也不声称可加载。

## 运行

游戏路径是占位示例；研究输入必须已经在本机准备好。

```powershell
python scripts/fc27_stage_loader.py --game-root 'X:\Games\EA SPORTS FC 27' --bundle 'local\my-build' --fc27-export 'local\my-export' --package 'local\my-package' --output 'local\my-loader-stage'
python scripts/fc27_stage_loader.py --game-root 'X:\Games\EA SPORTS FC 27' --bundle 'local\my-build' --fc27-export 'local\my-export' --package 'local\my-package' --verify 'local\my-loader-stage'
```

第一条创建副本并从磁盘审查；第二条独立从绑定基线重建，逐字节核对全部副本、重新审查挂载范围，并严格比较报告。成功退出码 0 不表示游戏接受。

已有单命令流水线可以追加此阶段：

```powershell
python scripts/fc27_pipeline.py --config 'local\pipeline-config.json' --module role_execution --stage-loader --output 'local\loader-pipeline-01'
```

配置模板仍为 `resources/pipeline-config.example.json`；本机路径配置留在 local。`--stage-loader` 串联研究、编译、打包、打包重建、加载副本生成、加载副本重建；失败阶段写入流水线报告，后续阶段不继续。`completed` 只代表所选离线阶段完成。

## 封装头的实际障碍

公开 FMT.Core 2026.14.1 基础 TOCFileWriter 的默认方法与本机格式存在以下差别，不能直接作为 FC27 写入器使用：

| 部分 | 公共基础写入器默认方法 | 本机 FC27 观测与自有候选 |
| --- | --- | --- |
| CAS Bundle 位置头 | 写出 8 个 UInt32，共 32 字节 | 9 个 UInt32，共 36 字节，末项重复数量 |
| 显式 CAS 标识 | 0、isPatch、catalog、cas，共 4 字节 | 分层 UInt64，共 8 字节 |
| 标识标记 | CasIdentifier 默认 1 | 当前数据中为 0x80/0x84，0 为继承 |

这只是公共基础类的静态证据：其方法可被派生类覆盖，不能据此断言所有 FMT 版本或插件都不支持新格式。本轮没有取得可确认 FC27 写入的派生实现。文本保留在本机 `notes/profile-study/TOCFileWriter.cs`；自有索引编译器根据原始 FC27 数据生成 36 字节位置块和 UInt64 标识，保持原 0x84 未知位。

静态读取公开 [FMT.Core 2026.14.1](https://www.nuget.org/packages/FMT.Core/2026.14.1) 的 TOCFileSignatureWriter，发现其默认对头后 556 字节以后的内容调用签名生成，并将结果写到偏移 8。该类是静态研究对象，没有被加载或执行；文本保留在本机 `local/research/2026-10-08/notes/profile-study/TOCFileSignatureWriter.cs`。

本机 FC27 四份改动索引均有非零的 256 字节区域，其位置与上述公开组件的写入位置相符。当前候选的载荷已改变，但原版封装头和该区域仍逐字节保留。因此报告逐份列出 `payload_changed`、`header_preserved`、`signature_region_preserved`，并将 `signature_validity_verified` 固定为 false。

这能确定我们还没有处理改动后的封装接受性，不能确定 FC27 实际执行什么验证，也不能把前代算法、通用空头或改名 profile 当成当前版本的解决方案。没有提取或分发签名私钥，没有伪造签名或修改游戏验证逻辑。另见 [加载器兼容性证据](loader-compatibility.md)。

只读扫描 FC27.exe 的 ASCII/UTF-16 字面量时，没有找到完整 `-dataPath` 或 `ModData`，找到了 `layeredInstallChunkFiles`；文件 SHA256 和偏移记录在本机 `notes/loader-binary-string-evidence-v1.json`。字符串可能被拆分、生成或位于其他组件，缺失不能证明启动参数不受支持，存在也不能证明实际加载行为。因此没有照搬前代启动参数或生成启动脚本。

## 2026-10-08 本机结果

全仓 145 项测试通过，覆盖目录和回退边界、跨层注册表读取、载荷与报告损坏、元数据漂移、上限和安装目录写入拒绝；后续新增 [管理核心](mod-manager.md) 的合并、登记与应用/还原事务检查。

六组实验的副本位于 `local/research/2026-10-08/candidates/whole-match-loader-stage-v1/`：81 份文件、97,473,243 字节。其中 6 份为本次索引和 CAS 候选，75 份为其他原版元数据副本；另列出 85 份原版 CAS 回退文件。独立重建通过。两份 fcgame.toc 的 30 个 Bundle 共检查 327,337 个位置，327,303 个指向明确的原版回退，34 个指向副本。16 份选定资产在基础/补丁中的 32 次引用全部读取候选载荷并核对散列。

另生成 `local/research/2026-10-08/candidates/loader-probe-v1/` 最小编译候选，只修改当前 FC27 的 `GroundPassDecisionDistToTargetMod` 一个 Float32：1.5 到下一个可表示的数 1.5000001192092896。资产仅改变 1 字节，完整反向恢复通过；基础/补丁两次引用都从副本读取。这是隔离格式问题的诊断候选，不是 AI 优化参数或可感知效果承诺。计划、原始资产、编译包、副本与报告均留在 local。

带 `--stage-loader` 的实际角色执行模块流水线另见 `local/research/2026-10-08/candidates/loader-pipeline-v1/`。

当前未启动游戏、未写入安装数据、未创建文件链接，也未执行 FMT 或 Mod Manager。下一步仍需取得 FC27 封装处理、实际加载器映射和部署/回退机制的可靠依据；实际加载与比赛效果需要独立实机证据。
