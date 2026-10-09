"""Windows 启动偏好仅保存工作区位置；研究数据仍全部在外部 local。"""

import os
from pathlib import Path
from fc27_runtime import validate_workspace


KEY = r"Software\FC27CareerLab\Manager"


def last_workspace():
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, _ = winreg.QueryValueEx(key, "Workspace")
        return validate_workspace(Path(value))
    except (OSError, ValueError, TypeError):
        return None


def remember_workspace(path):
    if os.name != "nt":
        return
    import winreg
    path = validate_workspace(path)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Workspace", 0, winreg.REG_SZ, str(path))
