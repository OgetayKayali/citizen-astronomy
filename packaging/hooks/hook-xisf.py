"""PyInstaller hook for the single-module xisf package and its binary deps."""

from PyInstaller.utils.hooks import collect_dynamic_libs, copy_metadata

hiddenimports = [
    "lz4",
    "lz4.block",
    "zstandard",
    "zstandard.backend_cffi",
]

datas = copy_metadata("xisf")
binaries = collect_dynamic_libs("lz4") + collect_dynamic_libs("zstandard")
