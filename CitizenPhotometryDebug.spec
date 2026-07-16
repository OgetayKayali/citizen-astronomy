# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\photometry_app\\main.py'],
    pathex=['c:\\Users\\Kay\\Desktop\\Projects\\Photometry'],
    binaries=[],
    datas=[
        ('c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\README.md', '.'),
        ('c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\DOCUMENTATION.md', '.'),
        ('c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\guides\\hr_diagram.md', 'guides'),
        ('c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\guides\\differential_photometry.md', 'guides'),
        ('c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\guides\\asteroid_comet_detection.md', 'guides'),
        ('c:\\Users\\Kay\\Desktop\\Projects\\Photometry\\guides\\transient_finder.md', 'guides'),
    ],
    hiddenimports=['xisf', 'pyqtgraph', 'pyqtgraph.opengl', 'OpenGL', 'OpenGL.GL', 'OpenGL.platform.win32', 'OpenGL_accelerate', 'astroquery.vizier', 'astroquery.ipac.nexsci.nasa_exoplanet_archive', 'astropy.tests', 'astropy.tests.runner', 'scipy.special._cdflib', 'imageio', 'imageio.v2', 'imageio.plugins.ffmpeg', 'imageio_ffmpeg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'matplotlib.tests', 'astroquery.dace'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CitizenPhotometryDebug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CitizenPhotometryDebug',
)
