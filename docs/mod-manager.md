# FC27 离线模组管理核心

第一版以 Python 标准库实现命令行管理核心，整合现有研究、定点编译、打包及加载副本校验。支持登记、列表、重新预检、冲突预览、计划合并重编译，以及管理器自己的项目内副本应用/还原演练。后续已接入 [中文本地界面](manager-ui.md)；没有真实游戏安装或启动命令。封装签名、加载器兼容性及比赛效果继续显示为未验证。

## 管理目录

默认目录为被 Git 忽略的 `local/mod-manager/`：

```text
mod-manager/
├── manager.json              # 仅引用项目 local 内的本机流水线配置
├── .operation-lock           # 操作系统文件锁；进程退出后释放
├── library/<id>.json          # 登记内容、内容散列、来源报告与版本绑定
├── builds/<id>/               # 管理器构建或合并的候选；保留失败构建供检查
└── rehearsals/<run>/
    ├── transaction.json       # 原始/候选文件集、修改清单及应用/恢复阶段
    ├── backup/Data|Patch/     # 本事务覆盖前的完整原始索引副本
    └── target/ModData/        # 管理器自己创建的演练目标
```

登记引用已存在的研究目录，不复制整个候选。来源目录应保留供后续预检与重编译使用。记录绑定构建、打包、加载副本报告，编辑计划、原始索引版本和源文件清单；使用前会从实际基线重建检查。列表仅显示登记快照，明确标记没有进行本次实时预检。

路径只能位于本项目 local 内，拒绝游戏安装目录、目录/文件链接、硬链接和不规范相对路径。标识只能使用 1 至 48 位小写字母、数字、连字符；不得使用 Windows 保留名称。已有标识、构建目录或演练目录均不覆盖。游戏只作为显式配置的读取来源；存档、账号目录与启动器配置不在扫描范围。

## 初始化与登记

先根据 `resources/pipeline-config.example.json` 准备本机 local 配置。下面的路径是占位示例。

```powershell
python scripts/fc27_manager.py init --config 'local\pipeline-config.json'
python scripts/fc27_manager.py register --id example --title '实验候选' --bundle 'local\my-build' --export 'local\my-export' --package 'local\my-package' --stage 'local\my-loader-stage'
python scripts/fc27_manager.py list
python scripts/fc27_manager.py check --id example
```

登记前必须通过完整加载副本重建与资源审查，不能只提供一个扩展名正确的 fbmod。登记的内容散列检测意外变更，不是数字签名或第三方来源认证；不能据此信任任意外来包。

`--root 'local\other-manager'` 可放在子命令前，选择另一个项目内管理目录。命令成功退出码为 0，错误为 1；状态中的 `preflight_passed` 只表示离线检查通过。

## 构建与合并

```powershell
python scripts/fc27_manager.py build --id transitions-01 --title '攻守转换实验' --module transitions
python scripts/fc27_manager.py preview --ids example transitions-01
python scripts/fc27_manager.py compose --id combined-01 --title '合并实验候选' --ids example transitions-01
```

build 串联现有完整流水线，强制生成并核对加载副本，成功后登记。`--module` 接受 `all` 或资源研究方案中的一个模块 id。

多个候选即使修改不同球员字段，也会共用 layout、TOC 或 CAS 路径，不能直接叠加编译文件。preview 重新预检每个登记，列出共同资产与共同输出路径；共用路径不是字段冲突，但要求重新编译。

compose 合并每份已验证的 `plan.json`，然后从原始 FC27 基线重新构建、重新分配 CAS、打包、生成加载副本并登记新结果。同名字段的相同完整编辑项去重；不同修改值或预期原值冲突时拒绝。导出、SDK、共享类型、原始资产、完整根类型身份或游戏版本绑定不同也拒绝。合并最多 64 项模组、64 份资产、8,192 项编辑。preview 的 `plans_mergeable` 仅表示计划合并检查通过；完整类型、字节别名及修改边界仍由后续真实编译检查。

## 项目内应用与还原演练

```powershell
python scripts/fc27_manager.py rehearse --id combined-01 --run trial-01
python scripts/fc27_manager.py status --run trial-01
python scripts/fc27_manager.py restore --run trial-01
```

rehearse 不接受任意目标路径，只创建 `rehearsals/<run>/target/ModData/`。先重新预检候选，并将原版非 CAS 元数据复制到新的演练目标；原版巨大 CAS 不复制，仍用已声明的只读回退。覆盖前保存所有原始索引完整字节，并写入事务日志，再应用候选索引和新 CAS。目标文件集与登记加载副本完全相同时，重新进行离线资源位置和选定载荷审查。

restore 先核对登记绑定、所有备份及当前演练文件，再恢复原始索引，仅移除本事务明确新增且内容吻合的新 CAS。恢复后的全部文件必须与应用前文件清单和字节散列相同。事务记录保留，重复恢复会重新核对并返回相同的已恢复状态。restore 不重新预检游戏版本，不读取原版 CAS，依赖管理配置、登记记录、日志和自己的备份；它仍只恢复演练副本。

日志包含 prepared、applying、applied、restoring、restored 阶段。在已有完整日志和备份的应用/恢复异常后，允许文件分别处于原始或候选状态，再次 restore 可以继续恢复。未知文件、手动更改、备份损坏或日志与登记不符时停止，避免覆盖手动内容。创建副本期间失败但尚未产生完整日志的目录不会自动复用，须保留检查并使用新的演练标识。

操作系统锁将管理操作串行化；不会按超时抢占其他进程。事务在单个文件上使用写临时文件、刷新后替换，整批文件依靠备份和日志恢复，不宣称整个目录的原子替换或断电后自动恢复。

## 验证范围

全仓 145 项测试通过，其中 14 项管理器测试覆盖计划去重和字段冲突、版本/完整身份差异、路径边界、local 根目录与目标父目录链接拒绝、互斥锁、登记内容修改、原版版本漂移、实际格式登记与演练、游戏数据保持原值、备份/目标损坏拒绝、应用中断还原、恢复中断续作及日志伪改拒绝；另有 14 项本机界面服务测试。

```powershell
python -m unittest discover -s tests -v
```

## 2026-10-08 本机结果

默认管理目录已初始化，登记最小诊断候选 `probe` 与角色执行候选 `roles`。两者修改不同资产，但预览发现 6 个共同输出路径，要求合并计划重新编译。

`probe-roles` 完成实际合并、重新编译、打包、加载副本重建和登记：2 份资产、8 项数值修改、22 个变更字节。`trial-01` 演练保存 4 份原始索引备份，应用 6 个覆盖/新增文件，副本从 79 份原始元数据成为 81 份文件，离线位置与载荷检查通过。随后恢复 4 份索引、移除 2 份本事务新增 CAS，79 份目标文件全部与应用前的散列清单相同；备份和事务日志保留。

`transitions-01` 经管理器单命令完成攻守转换模块的研究、编译、打包、加载副本校验和登记：3 份资产、30 项数值修改、58 个变更字节。上述核心验证共登记 4 项候选，后续 [界面验证](manager-ui.md) 另构建一项角色执行候选。

本机证据保存在被 Git 忽略的 `local/research/2026-10-08/notes/mod-manager-validation-v1.json`。无论命令完成多少离线阶段，`loadable_mod`、`loader_compatibility_verified`、`gameplay_effect_verified` 和 `game_files_written` 都保持 false。当前管理器用于管理可审查的实验候选，不能将其称为已经能在 FC27 中运行的成品。剩余兼容性见 [加载适配说明](loader-adaptation.md)。
