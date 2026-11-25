"""What differs by OS: Hancom, revealing a file, opening with the default app, settings folder (SPEC.md §6)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "GovParse Desktop"


def settings_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    if sys.platform == "darwin":
        return Path(os.environ.get("HOME") or Path.home()) / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path(os.environ.get("HOME") or Path.home()) / ".config") / "govparse-desktop"


class CurrentPlatform:
    def has_hancom(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg

            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"HWPFrame.HwpObject"))
            return True
        except OSError:
            return False

    def reveal(self, path) -> None:
        path = str(path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) or "."])

    def open_default(self, path) -> None:
        path = str(path)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - user asked to open the file
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
