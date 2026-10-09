# 单文件、普通权限、无控制台；禁止收集游戏文件和研究依赖。
from pathlib import Path
import os
import sys
import json

ROOT = Path(SPECPATH).resolve().parents[1]
licenses = Path(os.environ['FC27_BUILD_LICENSES']).resolve(strict=True)
audit = Path(os.environ['FC27_BUILD_AUDIT']).resolve()
allowed_tools = ROOT / 'local/packaging/venv'
if Path(sys.prefix).resolve() != allowed_tools.resolve():
    raise ValueError('请使用项目 local/packaging/venv 内的隔离构建环境')

a = Analysis(
    [str(ROOT / 'scripts/fc27_desktop.py')],
    pathex=[str(ROOT / 'src'), str(ROOT / 'scripts')],
    binaries=[],
    datas=[(str(ROOT / 'resources/ui'), 'resources/ui'),
           (str(ROOT / 'resources/whole-match-study.json'), 'resources'),
           (str(ROOT / 'resources/pipeline-config.example.json'), 'resources'),
           (str(licenses), 'licenses')],
    hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3', 'gi', 'webview.platforms.cef',
              'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
              'webview.platforms.android'],
    noarchive=False,
)

# 分析完成、创建 EXE 之前，核对每个被收集文件的实际来源。
approved = [ROOT / 'src', ROOT / 'scripts', ROOT / 'resources/ui', licenses,
            allowed_tools, Path(sys.base_prefix).resolve()]
collected = []
for kind, items in [('module', a.pure), ('binary', a.binaries), ('data', a.datas)]:
    for name, source, tag in items:
        if not source:
            continue
        if kind == 'module' and source == '-':
            # PyInstaller 对 namespace package 的占位符，不对应输入文件。
            continue
        path = Path(source).resolve(strict=True)
        generated_library = kind == 'data' and name == 'base_library.zip' and path == audit.parent / 'fc27-manager/base_library.zip'
        public_template = path in {ROOT / 'resources/whole-match-study.json', ROOT / 'resources/pipeline-config.example.json'}
        if not generated_library and not public_template and not any(path.is_relative_to(base.resolve()) for base in approved):
            raise ValueError('未经批准的打包输入：' + name)
        collected.append({'name': name, 'kind': kind, 'source': str(path), 'type': tag})
audit.write_text(json.dumps(collected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='FC27Manager',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, icon='NONE',
          version=os.environ['FC27_BUILD_VERSION_INFO'],
          uac_admin=False, uac_uiaccess=False)
