# PyInstaller spec — standalone Windows build for cratedig (onedir).
#
# Build (from the repo root, with the dev extras installed: `pip install -e ".[dev]"`):
#     pyinstaller cratedig.spec --clean --noconfirm
# Output: dist/cratedig/  — a folder containing cratedig.exe + its dependencies. Zip that folder
# and attach it to a GitHub Release.
#
# onedir (not onefile) is deliberate: a onefile exe unpacks to %TEMP% on every launch and is far
# more likely to be flagged as a false-positive by Windows Defender. FFmpeg is NOT bundled —
# users install it separately (winget install Gyan.FFmpeg) and it is found on PATH, exactly as
# with the pip install.

from PyInstaller.utils.hooks import collect_all

# yt-dlp imports its extractors lazily, so a plain analysis misses them and YouTube
# search/download would break in the frozen exe. collect_all pulls every submodule + data file.
# certifi is collected too so requests has its CA bundle for HTTPS at runtime.
datas = []
binaries = []
hiddenimports = []
for _pkg in ("yt_dlp", "certifi"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ["packaging/cratedig_entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir: dependencies live in the COLLECT folder, not inside the exe
    name="cratedig",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-compressed exes draw MORE AV false-positives; keep it off
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
    upx=False,
    upx_exclude=[],
    name="cratedig",
)
