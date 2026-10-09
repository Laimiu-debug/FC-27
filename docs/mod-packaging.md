# FC27 实验容器与索引编译

已生成具有真实资源表、散列和载荷表的 Frosty v6 `.fbmod` 实验容器，并根据本机 FC27 文件编译 Bundle 元数据、TOC 位置表、layout 分层 CAS 注册表和新 CAS 副本。当前加载器的 FC27 profile 映射、封装接受性和比赛效果均未验证；这些输出不是可以直接安装的傻瓜包。

## 格式依据与范围

容器依据 [FrostyMod 原始源码](https://github.com/FrostyToolsuite/FrostyToolsuite/blob/master/FrostyModSupport/Mod/FrostyMod.cs) 及 [BaseModResource](https://github.com/FrostyToolsuite/FrostyToolsuite/blob/master/FrostyModSupport/Mod/Resources/BaseModResource.cs) 的公开格式独立实现。本机研究快照及许可保留在 `local/`。仅支持已有 EBX 资产、无 handler、无增删 Bundle 的固定长度数值候选。

容器按小端写入 magic `0x01005954534F5246`、版本 6、数据表位置及数量、profile 字符串、当前补丁 layout 的 head、六项 UTF-8 元数据和资源段 SHA1。每项资源包含类型、载荷索引、名称、原始大小及压缩载荷 SHA1；数据表使用相对偏移和长度。总文件最多 32 MiB、64 项资源。独立黄金字节测试核对字段顺序和中文元数据的实际字节长度，读取器检查范围、载荷散列、解压结果、唯一名称及完整消费。

Anth FC26 参考样本使用紧凑 FETM v1 容器；历史 FMT 的 FIFAMod v28 又是另一格式。本组件没有实现这两种写入，也没有将其头部改名为 FC27。`--profile FC27` 只是显式研究标签；Frosty 读取器需要与当前选用 profile 的名称严格相等，目前没有相应 FC27 加载配置的确认依据。

## 索引编译

从已验证定点构建及其绑定的原始导出清单开始，重新核对安装数据的四份 layout/TOC 散列。扫描基础和补丁 `fcgame.toc` 的全部 Bundle，逐个寻找目标引用，检查原始尺寸、压缩 SHA1 和实际 CAS 载荷；目标在任一引用中存在不同版本时拒绝猜测替换。扫描范围不包括其他超级包，不能宣称检查游戏全部资源。

每个修改 Bundle 仅改目标 EBX 的 SHA1/原始大小字段，保留其他 EBX、RES、Chunk、字符串表及未知字节。修改后的元数据和资产块写入项目内新 CAS；新编号同时避开磁盘文件、layout 注册表和所扫描 TOC 中已有的引用。没有复制巨大的原始 CAS 文件。

TOC 追加新位置块，只改对应表项的尺寸与相对位置。旧位置块和原封装头保留，资源数量保持原值；重定位后，所有未选位置都必须与原值相同。尤其检查原来继承 CAS 标识的后续记录，必要时插入显式标识，防止其错误指向新文件。现有 `0x84` 标记保留，其中未知位的引擎含义未验证。

layout 只向 `layeredInstallChunkFiles` 增加小端 UInt64 新 CAS 标识并排序。重新读取后将此字段恢复为原值，必须重编码成完整原始 layout，以证明其他字段和封装头保持原样。未知封装内容不解释、不重新签名，自有解析器接受不代表游戏接受。

这些索引副本保留大量原版 CAS 引用，需要实际加载器提供原版数据回退。已新增 [项目内 ModData 副本与原版回退清单](loader-adaptation.md)，但没有建立文件链接、启动参数或独立完整 ModData；工具不运行游戏加载器，不绕过反作弊。

## 运行与产物

下面的游戏路径为占位示例。所有命令只读游戏文件，输出只能新建在项目 `local/` 下。

```powershell
python scripts/fc27_package.py --game-root 'X:\Games\EA SPORTS FC 27' --bundle 'local\research\2026-10-08\candidates\fixed-build-v2' --fc27-export 'local\research\2026-10-08\extracted\fc27-attrib-v3' --profile FC27 --output 'local\candidates\native-package-01'
python scripts/fc27_verify_package.py --game-root 'X:\Games\EA SPORTS FC 27' --bundle 'local\research\2026-10-08\candidates\fixed-build-v2' --fc27-export 'local\research\2026-10-08\extracted\fc27-attrib-v3' --package 'local\candidates\native-package-01'
python -m unittest discover -s tests -q
```

打包目录包含：

- `candidate.fbmod`：公开 Frosty v6 格式的实验容器。
- `index-candidate/Data/`、`index-candidate/Patch/`：本次需要修改的 layout/TOC 副本和新 CAS。
- `index-original/`：所改 layout/TOC 的完整原始副本，绑定原始散列。
- `package-report.json`：构建与导出清单散列、原始索引版本、所有目标 Bundle 引用、文件散列和未验证事项。

验证命令从绑定的原始基线重新编译，然后比较报告和磁盘上每份产物的完整字节，拒绝缺失文件、额外文件、报告伪称已兼容、载荷损坏或版本改变。它同时检查底层定点构建仍可逐字节反向恢复；不调用任何第三方应用。成功退出码为 0，错误为 1。

`index-original/` 与定点构建的 `original-assets/` 仅用于离线副本恢复和对照。尚无安装操作，因此也没有游戏安装或存档恢复命令。生成物包含本机游戏资产，留在 Git 忽略目录，不作为第三方资源分发包提交。

## 2026-10-08 结果

89 项测试通过。新增验证覆盖容器黄金字节、中文偏移、资源段和载荷损坏、负长度与范围、重复路径、受限资源种类、handler/Bundle 操作拒绝、位置继承修复、未选资产保留、错误基线、layout 注册表、磁盘编号冲突、先验证后输出、安装目录写入拒绝及完整打包流程。

本机产物在 `local/research/2026-10-08/candidates/native-package-v1/`。两份真实 FC27 资产的 16 项修改、38 个变更字节已打包；基础 Bundle 22 与补丁 Bundle 1 的全部目标引用均处理。两个新 CAS 标识分别为 `0000a3a00de30004`、`0001a3a00de30002`，补丁 head 为 `3770653`。11 份产物从原始基线重建后逐字节相同，原始索引副本与已绑定来源相同。

参数仍是跨版本移植的编译实验，尚未形成整场比赛的正式优化方案。接下来需要确认 FC27 加载器映射和部署结构，补足尚未支持的字段，并研究进攻、防守、接应与攻守转换的参数组合。用户已停止实机验证，当前继续离线工作；不能据此承诺加载成功、比赛改善或账号风险为零。

同日已扩展出六组整场比赛实验方案，将 16 份资产的 147 项数值修改、298 个变更字节打包到 `local/research/2026-10-08/candidates/whole-match-package-v1/`，11 份产物重建比对通过。新增 [整场比赛研究工具与单命令流水线](whole-match-study.md)，全仓测试现为 145 项；上述两资产包是早期验证记录。加载器复查结果见 [兼容性证据](loader-compatibility.md)，最新目录、回退和最小诊断候选见 [加载适配说明](loader-adaptation.md)，候选登记与计划合并见 [管理核心](mod-manager.md)。
