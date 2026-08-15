"""PyInstaller hook for rawpy and its LibRaw native libraries."""

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = collect_all("rawpy")
datas += copy_metadata("rawpy")
hiddenimports = list(dict.fromkeys([*hiddenimports, "rawpy"]))
