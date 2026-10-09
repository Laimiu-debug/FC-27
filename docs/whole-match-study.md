# 整场比赛的六组研究方案

已把两资产编译实验扩展成六组明确方案，在实际 FC27 数据上生成 16 份资产、69 个根字段的实验计划，共 147 项数值修改、298 个变更字节。合并包的容器、Bundle/TOC/layout 索引及 CAS 副本通过磁盘重建比对。这里的“整场比赛”表示研究覆盖范围，不表示已经修复整场比赛的 AI。

参考是完整 SHA256 已校验的 Anth FC26 FREE V5 REGULAR 修改样本；没有对应前代原版基线，因此差异同时包含版本变化和模组变化。字段作用根据 SDK 名称和资产分组推断，时间单位、难度/模式分支、数组列含义与实际效果尚未验证。方案不修改菜单滑块，也不重写封闭游戏的决策代码；它修改已解析的内部玩法资产数值。

## 模块及当前结果

| 模块 id | 范围 | 资产 | 根字段 | 修改值 | 变更字节 |
| --- | --- | ---: | ---: | ---: | ---: |
| ball_decisions | 持球传球、线路与空间评分、提前决定和传球延迟 | 3 | 16 | 26 | 59 |
| offball_support | 预测范围、斜向空位、边路和回传接应区域 | 5 | 12 | 24 | 45 |
| defender_reactions | 分难度防守反应曲线、盯防评价和目标速度 | 1 | 5 | 26 | 44 |
| team_defense | 盯防响应、团队截线活动范围和压迫盯防 | 3 | 12 | 34 | 71 |
| transitions | 转换阶段时间、进攻反应和各位置回撤速度 | 3 | 17 | 30 | 58 |
| role_execution | 角色熟悉度对应的接应与盯防执行速度 | 1 | 7 | 7 | 21 |
| 合计 | 六组均可单独生成计划，也可合并 | 16 | 69 | 147 | 298 |

明确资产和字段清单见 [研究配方](../resources/whole-match-study.json)。没有对 336 个同名资产批量复制全部差异，未支持的名称和结构也不会静默变成优化项。每个资产采用原子筛选：任何选中字段不兼容，整个资产标为 rejected，并记录原因。

几个差异说明为什么不能直接宣称优化：

- `positiveTransitionBaseTime` 为 FC27 的 4.0 与参考的 3.0；`negativeTransitionBaseTime` 为 6.0 与 8.0。状态触发和单位未验证，不能直接断言转换更聪明。
- Legendary 防守反应曲线首点为 54.0 与 64.0，WorldClass 部分后段则更小。参考不是所有难度都“反应更快”，需要分难度评估。
- `GroundPassDecisionMinCourseSafety` 为 0.99 与约 1.98。名称像安全阈值，但它可能是评分系数或另一范围的量，不能当成 0 到 1 概率。

## 新增移植校验

当前资产由本机共享类型描述和 EFIX/EBXX 解析，前代资产由完整 GUID/签名对应的 SDK 解析。根 GUID、根名称哈希和字段名称哈希必须对应；签名有变化时必须有当前共享布局依据。编辑计划仅填写字段路径、当前原始位和参考原始位，偏移由编译器自行解析。

数值数组要求类型、数量及数组标识全部一致，仍不将这些条件视为列语义的证明。曲线要求 FloatCurve 和 FloatCurvePoint 的完整身份、名称哈希、大小、对齐与必须字段一致。点数、数组标识、MinX/MaxX、各点 X、X 切线和 CurveType 必须逐位相同；只研究 Y 和 Y 切线，完整字段一起检查，避免只复制 Y 后混用另一版本的切线。

拒绝未知名称、异常参考元数据诊断策略、非有限数值、部分字段缺失和不匹配的形状。每个可生成计划的资产都先在内存中完成当前编辑器的干跑和逐字节反向恢复；没有证实玩法合理性。

## 单命令流水线

从 [配置模板](../resources/pipeline-config.example.json) 复制到项目 `local/` 下，填写本机游戏路径。配置只保存本机路径，不提交仓库。其他输入也必须位于 `local/`，研究配方必须位于 `resources/`。本机已准备的配置在 `local/research/2026-10-08/notes/pipeline-config-v1.json`。

```powershell
python scripts/fc27_pipeline.py --config 'local\research\2026-10-08\notes\pipeline-config-v1.json' --module all --output 'local\candidates\whole-match-run-01'
python scripts/fc27_pipeline.py --config 'local\research\2026-10-08\notes\pipeline-config-v1.json' --module transitions --output 'local\candidates\transitions-run-01'
```

命令依次完成研究审查、选择合并或单模块计划、编译、打包以及从原始基线重建并比较磁盘产物。输出包含 `study/`、`built/`、`packaged/` 和 `pipeline-report.json`。配置错误、版本改变或所选模块有拒绝项时停止；运行中发生错误会保留已完成的本地阶段并记录 failed_stage，不继续打包，不覆盖旧目录。选择其他模块使用上表的明确 id。

`study/plans/` 中包含六个独立计划和 `combined.json`；`review.csv` 逐项记录 147 个变化的模块、资产、字段、编码、当前值、参考值和原始位。报告保留每个曲线的类型身份与几何校验依据，以及所有未验证状态。

单独研究命令为 `scripts/fc27_study.py`，需要显式提供 game-root、fc27-export、sample、sdk、shared-types、codec、recipe、output，参数与现有导出/构建工具一致。它只接受已校验参考样本及明确选择的字段；候选输出均位于项目目录。

## 本轮验证与限制

145 项测试通过，覆盖字段选择、跨版本名称哈希、曲线类型、X/定义域/X 切线变化拒绝、Y 与 Y 切线、数组标识、非有限数值、异常诊断策略、原子拒绝、模块路径与保留名称、配置边界、流水线顺序和失败状态；另覆盖加载副本、回退与管理器事务检查。

流水线可追加 `--stage-loader` 准备并验证项目内 ModData 副本、原版 CAS 回退清单与全部 fcgame Bundle 位置，见 [编译与离线加载适配](loader-adaptation.md)。这不是实际部署或游戏加载。

已生成 `local/research/2026-10-08/candidates/whole-match-study-v1/`、`whole-match-build-v1/` 和 `whole-match-package-v1/`，16 份资产均可恢复为原始副本。全部目标引用出现在基础 Bundle 22 与补丁 Bundle 1，11 份打包产物重建后逐字节一致。攻守转换的单命令流程在 `transitions-pipeline-v1/` 已独立通过，处理 3 份资产、30 个值、58 字节。

六组全部合并的单命令流程在 `whole-match-pipeline-v1/` 也已通过，生成与分步合并构建一致的 16 资产、147 值、298 字节实验；其 11 份打包产物均通过重建比较。

加载器的最新公开证据见 [兼容性记录](loader-compatibility.md)。没有启动游戏、执行加载器或写入安装目录；现阶段没有实际比赛的对照数据，也没有完整 FETM v1 写入或已验证的 FC27 部署方式。六组方案用于后续逐模块比较，当前保留全部实验标记，不能承诺 AI 效果或账号风险为零。
