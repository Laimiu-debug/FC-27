# FC27 加载器兼容性证据

2026-10-09 再次核对官方支持表，FC27 仍为只读支持，未取得可确认写入/加载的映射。进一步静态确认前代公开签名方法调用 HMACSHA1 与 RSA PKCS1 签名接口；没有复制、提取或使用私钥，也不能从前代实现推断 FC27 接受改后封装。免配置版本绑定预设仅解决参数准备与离线编译，加载缺口继续保留，见 [普通用户入口](player-entry.md)。

2026-10-08 重新检查公开资料，仍未取得可确认 FC27 写入与加载的完整工具链。因此本仓库继续将容器和索引输出标记为实验候选，`loadable_mod`、`loader_compatibility_verified` 和 `gameplay_effect_verified` 保持 false。

## 已核对依据

[FMT 官方支持表](https://github.com/FMTDev/FMT.Releases#supported-games) 将 PC FC27 列为 50% 支持，注明当前只读并限于 PRO 版，等待完整发布。这是发布者声明，不能将“列出了 FC27”解释成可以编译和部署玩法模组。

[FIFA Editing Toolsuite 官方下载页](https://www.fifaeditortool.com/download) 显示 v2.1.2。本机已下载包中静态 SDK 到 FC26，没有 FC27 SDK。仅凭下载页版本和通用管理器介绍，不能确认 FC27 兼容性；我们没有运行该管理器。

静态研究了独立公开的 NuGet `FMT.ProfileSystem 2026.14.1` 和 `FMT.Compilers 2026.12.0`。前者的已校验包 SHA256 为 `9f01ebef32d28bdc737ba5cb28fb15406ab4440ac89abdbb3ba30f4ea972640b`，CRC 通过；从包中只提取 30,720 字节的 `FMT.ProfileSystem.dll`。未加载或执行这些应用组件，ILSpy 仅读取方法和元数据。

ProfileManager 从外部目录及嵌入资源读取 profile JSON，再由 LoadedProfile 提供 Name、AssetLoader 和 AssetCompiler 等属性。该公共组件本身没有提供可核实的 FC27 配置映射。Compilers 中存在通用 Frostbite2025AssetCompiler 类，类名不能证明其能写入本机 FC27 格式。这些发现来自本机静态文本，不表示已枚举所有商业或预发布工具。

本仓库的 `.fbmod` 依据公开 Frosty v6 格式；FrostyMod.Load 会要求容器的 profile 字符串与当前加载配置名称完全相等。目前 `FC27` 是研究标签，不能把它当成已验证配置。索引副本还需要原版数据回退、正确加载器布局及封装接受性验证。

## 当前边界

继续使用自有离线解析、字段编辑和索引编译组件，不虚构 FC27 profile，不把 FC26 配置改名来宣称适配，也不绕过工具授权或游戏保护。先前静态读取 Mod Manager 的 Modding.dll 被 Windows 安全软件拦截，已停止使用该组件，未关闭安全软件或改路径规避拦截。

研究记录在本机 `local/research/2026-10-08/notes/profile-study/`。本轮仍不启动游戏、不建立安装目录链接、不执行加载器、不写入游戏目录。已有候选可以继续离线审查和构建；实际加载与玩法效果需要独立证据。

## 编译与加载准备进展

同日已补齐 [项目内加载副本与显式 CAS 回退核对](loader-adaptation.md)，六组实验 16 份资产的 32 次基础/补丁引用均从新副本读取，独立重建通过。另有一个数值、一个字节的最小诊断候选。此组件是自有只读挂载解析器，没有操作实际游戏加载器。

公开 FMT.Core 2026.14.1 的 TOCFileSignatureWriter 静态文本表明，默认读取偏移 556 后的内容生成签名，写到偏移 8。本机四份改动索引在对应的 256 字节区域均为非零；候选载荷改变但封装头保留。我们尚未确认 FC27 的具体验证行为，没有更新签名或证明游戏接受。`signature_validity_verified` 保持 false；目录准备和数十万个范围检查不能消除这项缺口。

同包基础 TOCFileWriter 的默认方法仍写出 32 字节位置头、4 字节 CAS 标识和默认标记 1；本机 FC27 实际为 36 字节头、8 字节标识及 0x80/0x84。这些虚方法可能被特定插件覆盖，当前未取得可确认 FC27 写入的派生实现。不能仅凭通用编译器类名断言适配已完成；自有候选依据本机结构生成。
