# FC27 定点编译与批量构建

已实现按显式计划修改受支持数值字段的离线编译组件。它支持标量、数值数组和已有 FloatCurve 点的数值，生成候选 EBX、独立 CAS 压缩块、修改报告与原始副本。输出尚不能由游戏直接加载。

## 定位与校验

构建计划绑定基线导出清单、名称 SDK、FC27 共享类型表的 SHA256。每个资产还绑定原始文件 SHA256、完整根类型 GUID/签名，以及每个待改字段的原始字节。编译器自行解析实际字段位置，不接受计划提供的偏移。

开始构建时会只读核对当前安装的基础及补丁 `layout.toc`、`fcgame.toc`，并核对共享描述清单所记录的 initfs 来源散列；版本来源改变时拒绝沿用旧计划。共享类型清单是本机一致性依据，不是发行方的数字签名。名称 SDK 仅作静态读取，当前构建无需加载解压库、编辑器或管理器。

编辑器拒绝未知名称、未支持字段、重复项、字节别名、非有限浮点数、超出整数位宽的值、非规范布尔编码和无变化项。前代样本的异常元数据诊断策略不允许用于候选写入。修改后检查完整文件长度、引用与其他块、已支持的未选数值和字段形状，再验证 EBX 重编码、CAS 解码及反向恢复。

所有资产通过后才创建输出目录。单次最多 64 个资产、8,192 项修改、64 MiB 原始资产。输入须位于项目 `local/`，输出须为其中的新目录；拒绝安装目录、越界路径和覆盖既有结果。

## 编辑计划

参考 [计划模板](../resources/fixed-edit-plan.example.json)，将占位内容替换为本机检查得到的值。模板不是可以直接应用的优化预设。

每项编辑使用明确字段路径，例如 `PredictionPointMinMovementDelta[0]` 或 `CPUAI_MarkerReactionAmount_BallDirectionChange.Points[4].Y`。`expected_hex` 必须填写该字段当前完整原始位，使用小写十六进制。候选值二选一：

- `value`：布尔、整数或有限浮点数，由字段实际位宽编码。Float32 报告展示实际编码后的值。
- `new_hex`：完整候选原始位，用于避免通过 JSON 十进制转换丢失原始浮点位。

曲线点数、指针、数组标识和文件长度保持不变。工具不自动重算曲线定义域、切线或枚举含义，也不验证单位、难度列、合法枚举范围和玩法合理性。参数方案需要独立研究，不能把格式校验当成 AI 改善证据。

## 运行与输出

下面的游戏目录是占位示例；命令只读安装元数据，不启动游戏。

```powershell
python scripts/fc27_build.py --game-root 'X:\Games\EA SPORTS FC 27' --fc27-export 'local\research\2026-10-08\extracted\fc27-attrib-v3' --sdk 'local\research\2026-10-08\extracted\fet-sdk\FC26SDK.dll' --shared-types 'local\research\2026-10-08\extracted\initfs-types-v2\Data\SharedTypeDescriptors.ebx' --plan 'local\plans\my-plan.json' --output 'local\candidates\my-build-01'
python scripts/fc27_verify_build.py --bundle 'local\candidates\my-build-01'
python -m unittest discover -s tests -q
```

输出目录包含：

- `original-assets/`：本次涉及资产的原始副本，用于离线恢复。
- `candidate-assets/`：修改后的 EBX。
- `casblocks/`：候选对应的独立无压缩 CAS 块。
- `plan.json`：本次计划副本。
- `build-report.json`：来源散列、字段位置、修改前后原始位、数值与验证状态。

独立校验命令只读这些文件，核对计划副本与报告的散列、原始副本、候选、CAS 块、修改数量及反向恢复结果。成功退出码为 0，失败为 1。`src/fc27_edit.py` 的 `revert_values` 可在内存中将报告对应的候选逐字节恢复为原始 EBX；原始散列或候选散列不符时拒绝恢复。这里的恢复仅针对候选副本，不是游戏安装或存档恢复工具。

## 2026-10-08 验证结果

72 项测试通过，新增覆盖数值位宽、Float32 原始位、曲线 Y 编辑、别名、错误基线与类型身份、异常参考策略拒绝、批量校验先于写出、旧安装索引拒绝、共享表来源变化、输出边界、原始位的非有限数拒绝，以及损坏候选或修改报告与计划不符时的独立检查。

本机真实多资产结果在 `local/research/2026-10-08/candidates/fixed-build-v2/`：预测点数组移植 15 项参考原始位；CPU 盯防参数中 `CPUAI_MarkerReactionAmount_BallDirectionChange.Points[4].Y` 从 `1.0` 改为参考位对应的约 `0.99`。两项资产合计修改 16 个值、38 字节，均可反向恢复为原始文件，独立磁盘校验已通过。该曲线的点数、X、CurveType、MinX、MaxX 已与参考分别核对，保留 FC27 原始布局和引用。

参考来自已校验的 Anth FC26 样本；本次选中载荷重新解码并核对与先前研究副本一致。来源记录在本机 `notes/fixed-build-plan-provenance-v1.json`。这些跨版本差异包含游戏版本变化，不能直接归因于模组优化。本次只验证编译与恢复，没有启动或操作游戏，没有写入安装目录。

## 后续工作

定点构建之后已补齐受限 Frosty v6 `.fbmod` 容器与 FC27 Bundle/TOC/layout 注册表、新 CAS 的索引候选编译，见 [模组打包说明](mod-packaging.md)。完整 FETM `.fifamod` 写入、结构大小变化、引用重建和加载器适配仍未完成，没有完整 ModData 或加载证明；用户已要求停止实机验证，后续产物继续标注未实测。
