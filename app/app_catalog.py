"""Discover launchable app bundles without traversing their embedded helpers."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re


APPLICATION_ROOTS = (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications"),
                     Path("/System/Library/CoreServices"))

# Familiar spoken names supplement actual installed bundle names.
APP_ALIASES = (
    ("Finder", "com.apple.finder", ("finder",)),
    ("Terminal", "com.apple.Terminal", ("terminal",)),
    ("Visual Studio Code", "com.microsoft.VSCode", ("vs code", "vscode", "visual studio code", "code")),
    ("Safari", "com.apple.Safari", ("safari",)),
    ("Google Chrome", "com.google.Chrome", ("chrome", "google chrome")),
    ("System Settings", "com.apple.systempreferences", ("settings", "system settings")),
    ("Notes", "com.apple.Notes", ("notes",)),
    ("TextEdit", "com.apple.TextEdit", ("textedit", "text edit")),
)


def discover_applications(roots=APPLICATION_ROOTS) -> tuple:
    """Read app metadata in standard install folders; deduplicate by bundle identity."""

    apps = {}
    for root in roots:
        for directory, folders, _ in os.walk(root):
            bundles = sorted(name for name in folders if name.casefold().endswith(".app"))
            folders[:] = sorted(name for name in folders if name not in bundles and not name.startswith(".")
                                and not name.casefold().endswith((".framework", ".bundle", ".xpc", ".plugin", ".appex", ".prefpane")))
            for name in bundles:
                path = Path(directory) / name
                try:
                    with (path / "Contents/Info.plist").open("rb") as handle:
                        info = plistlib.load(handle)
                except (OSError, ValueError, plistlib.InvalidFileException):
                    continue
                if not isinstance(info, dict) or info.get("LSBackgroundOnly"):
                    continue
                identifier = info.get("CFBundleIdentifier")
                display = info.get("CFBundleDisplayName") or info.get("CFBundleName") or path.stem
                if (not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)+", identifier)
                        or len(identifier) > 240 or not isinstance(display, str) or not 1 <= len(display) <= 240):
                    continue
                aliases = {display.casefold(), path.stem.casefold()}
                aliases.update(alias for _, bundle, names in APP_ALIASES if bundle == identifier for alias in names)
                if identifier in apps:
                    aliases.update(apps[identifier][2])
                apps[identifier] = (display, identifier, tuple(sorted(aliases)))
    return tuple(sorted(apps.values(), key=lambda item: (item[0].casefold(), item[1])))
