# FC 27 经理模式傻瓜包

为 FC 27 经理模式制作一个操作简单、说明清晰的整合包，让玩家更方便地完成配置和使用。

**当前阶段：整场比赛研究、离线编译与 Windows 桌面管理器。** 已从本机共享类型表取得当前布局，336 个同名参考资产均可读取受支持字段，累计 10,312 个字段，其中 9,885 个有已核对名称。六组方案覆盖持球决策、无球接应、防守反应、团队防守、攻守转换和角色执行，已在 16 份资产上编译 147 项修改，生成完整 Frosty v6 实验容器与 FC27 索引候选。新增 ModData 副本、显式原版 CAS 回退清单，以及带界面的候选登记、计划合并重编译和项目内备份/还原演练；154 项测试通过。封装签名接受性、实际加载器映射、游戏加载和比赛效果尚未验证，当前产物不能称为可用的 AI 优化成品。

## 项目方向

- 整理经理模式所需的功能、资源和配置。
- 简化安装、配置和启动流程。
- 在涉及游戏文件或存档的操作前，提供备份与恢复方式。
- 提供清晰的使用说明和常见问题解答。

上述内容是后续规划，具体功能与实现方式将在需求确认后确定。

## 目录结构

```text
FC-27/
├── docs/          # 需求记录与项目文档
├── resources/     # 资源与配置模板
├── scripts/       # 开发、构建与打包脚本
├── src/           # 程序源码
├── tests/         # 离线格式与文件操作边界测试
├── .editorconfig  # 编辑器约定
├── .gitattributes # Git 文本文件与换行约定
├── .gitignore     # 本地文件与生成物忽略规则
├── AGENTS.md      # 项目协作约定
└── README.md
```

索引与封装解析使用 Python 标准库；本机玩法资产的 Oodle 解压需要显式提供已校验的研究依赖。运行方法、验证范围和尚未完成的组件见 [离线工具链说明](docs/offline-toolchain.md)。当前界面使用原生 HTML/CSS/JavaScript 与本机 Python 服务，已通过 PyInstaller 和 pywebview 封装为 Windows x64 单文件 EXE，在独立窗口运行，无需安装 Python。

第一个离线候选的来源、字段差异和限制见 [字段适配实验](docs/field-adaptation.md)。候选 EBX 和 CAS 压缩块均留在本机 `local/`，没有安装到游戏，也没有加入仓库分发。

用户于 2026-10-08 要求停止实机验证，继续离线开发。历史基线检查和备份见 [实机测试记录](docs/live-testing.md)。新的批量构建命令、验证方法和限制见 [定点编译工具](docs/fixed-build.md)。

实验容器打包、索引重定位、磁盘重建校验与限制见 [模组打包说明](docs/mod-packaging.md)。本机已将两份实际资产的 16 项数值修改打包，并处理其在基础和补丁包中的全部引用；生成物保留在 `local/research/2026-10-08/candidates/native-package-v1/`，不含安装操作。

新的 [整场比赛研究方案](docs/whole-match-study.md) 支持六组独立计划与合并实验包；[单命令离线流水线](docs/whole-match-study.md#单命令流水线) 串联字段审查、编译、打包和验证。本机合并实验包位于 `local/research/2026-10-08/candidates/whole-match-package-v1/`。已复查的 [加载器兼容性证据](docs/loader-compatibility.md) 仍不足以确认 FC27 可以加载。

[编译与离线加载适配](docs/loader-adaptation.md) 已生成 81 份加载副本并列出 85 份原版 CAS 回退文件，支持独立重建验证。另有仅改一个数值、一个字节的最小诊断候选。流水线加 `--stage-loader` 可以准备和核对项目内副本；它不安装、不启动游戏，当前仍未解决封装接受性和实际引擎回退。

[中文管理界面](docs/manager-ui.md) 已接入 [离线模组管理核心](docs/mod-manager.md)，提供候选库、构建与合并、副本与恢复、工作区设置四个页面。双击 `dist/FC27Manager/FC27Manager.exe`，或根目录的 [启动管理器.cmd](启动管理器.cmd)，打开独立桌面窗口。程序自动连接项目内已有工作区，退出时等待当前任务结束；管理数据留在 `local/mod-manager/`。发行包、工作区选择和源码构建方法见 [Windows EXE 说明](docs/exe-packaging.md)。没有游戏安装或启动命令。

开发时仍可启动浏览器版：

```powershell
python scripts/fc27_ui.py --open
```

```powershell
python -m unittest discover -s tests -v
python scripts/fc27_offline.py --game-root 'X:\Games\EA SPORTS FC 27'
python scripts/fc27_export.py --game-root 'X:\Games\EA SPORTS FC 27' --report 'local\fc27-index.json'
```

游戏路径为占位示例。上述脚本只读检查安装数据，不启动游戏；实机检查另行记录。未实现模组安装或游戏文件写入。

## 资源管理

游戏安装文件、个人存档、备份和本地下载的研究素材不得提交；本机研究输出放在被 Git 忽略的 `local/`。提交资源前，先确认其来源、许可及分发方式。
