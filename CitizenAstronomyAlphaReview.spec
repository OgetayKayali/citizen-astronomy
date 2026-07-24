# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata


ROOT = Path(SPECPATH).resolve()
APP_NAME = "CitizenAstronomyAlphaReview"
HOOKS_DIR = ROOT / "packaging" / "hooks"


def data_file(relative_path: str, destination: str = "."):
    source = ROOT / relative_path
    if not source.is_file():
        raise FileNotFoundError(f"Required packaging file is missing: {source}")
    return (str(source), destination)


def data_tree(relative_path: str, destination: str):
    source_root = ROOT / relative_path
    if not source_root.is_dir():
        raise FileNotFoundError(f"Required packaging directory is missing: {source_root}")
    rows = []
    for source in source_root.rglob("*"):
        if source.is_file():
            rows.append((str(source), str(Path(destination) / source.relative_to(source_root).parent)))
    return rows


def optional_linux_data_file(relative_path: str, destination: str = "."):
    try:
        return [data_file(relative_path, destination)]
    except FileNotFoundError:
        if not sys.platform.startswith("linux"):
            raise
        print(f"Linux test bundle: optional runtime file is missing: {relative_path}")
        return []


def optional_linux_data_tree(relative_path: str, destination: str):
    try:
        return data_tree(relative_path, destination)
    except FileNotFoundError:
        if not sys.platform.startswith("linux"):
            raise
        print(f"Linux test bundle: optional runtime directory is missing: {relative_path}")
        return []


datas = [
    data_file("README.md"),
    data_file("LICENSE"),
    data_file("assets/citizen_astronomy.ico", "assets"),
    data_file("textures/milkyway_2020_4k_preview.png", "textures"),
    data_file("textures/constellation_figures_4k.tif", "textures"),
]
datas += optional_linux_data_file("textures/moon_lroc_color_16bit_srgb_8k.tif", "textures")
datas += optional_linux_data_file("textures/moon_ldem_16.tif", "textures")
datas += data_tree("guides", "guides")
datas += data_tree("photometry_app/data", "photometry_app/data")
datas += optional_linux_data_tree("assets/moon_tiles", "assets/moon_tiles")
_mode_launcher_assets = ROOT / "assets" / "mode_launcher"
if _mode_launcher_assets.is_dir():
    datas += data_tree("assets/mode_launcher", "assets/mode_launcher")
datas += optional_linux_data_tree(
    "textures/milky_way_tiles_32k_padded_lzw_benchmark",
    "textures/milky_way_tiles_32k_padded_lzw_benchmark",
)
datas += collect_data_files("astroquery", includes=["CITATION"])
datas += collect_data_files("astroquery.simbad", includes=["data/query_criteria_fields.json"])
datas += collect_data_files("photutils", includes=["CITATION.rst"])
datas += collect_data_files("pyvo.samp", includes=["data/*"])
datas += copy_metadata("photutils")
datas += copy_metadata("xisf")
datas += copy_metadata("lz4")
datas += copy_metadata("zstandard")

binaries = []
hiddenimports = [
    "xisf",
    "lz4",
    "lz4.block",
    "zstandard",
    "zstandard.backend_cffi",
    "pyqtgraph",
    "pyqtgraph.opengl",
    "OpenGL",
    "OpenGL.GL",
    "OpenGL_accelerate",
    "astroquery.vizier",
    "astroquery.simbad",
    "astroquery.simbad.core",
    "astroquery.imcce",
    "astroquery.imcce.core",
    "astroquery.jplhorizons",
    "astroquery.jplhorizons.core",
    "astroquery.jplsbdb",
    "astroquery.jplsbdb.core",
    "astroquery.ipac.nexsci.nasa_exoplanet_archive",
    "astropy.tests",
    "astropy.tests.runner",
    "scipy.special._cdflib",
    "photutils.geometry.core",
    "sklearn.cluster",
    "sklearn.ensemble",
    "sklearn.metrics",
    "sklearn.model_selection",
    "reproject",
    "reproject.interpolation",
    "reproject.interpolation.high_level",
    "imageio",
    "imageio.v2",
    "imageio.plugins.ffmpeg",
    "imageio_ffmpeg",
    "matplotlib.backends.backend_qtagg",
]

if sys.platform.startswith("win"):
    hiddenimports.append("OpenGL.platform.win32")
elif sys.platform.startswith("linux"):
    hiddenimports += [
        "OpenGL.platform.egl",
        "OpenGL.platform.glx",
    ]

for package_name in ("lz4", "zstandard", "pyqtgraph", "imageio_ffmpeg", "velopack"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    [str(ROOT / "photometry_app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(HOOKS_DIR)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "matplotlib.tests",
        "astroquery.dace",
        "cupy",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "citizen_astronomy.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
