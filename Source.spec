# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
import faster_whisper

project_dir = Path(SPECPATH)
faster_whisper_dir = Path(faster_whisper.__file__).resolve().parent
datas = collect_data_files("customtkinter")
datas += [(str(project_dir / "glossary.txt"), ".")]
binaries = [
    (
        str(faster_whisper_dir / "assets" / "silero_vad_v6.onnx"),
        "faster_whisper/assets",
    ),
]

a = Analysis(
    [str(project_dir / "app.py")],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Source",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Source",
)

app = BUNDLE(
    coll,
    name="Source.app",
    icon=str(project_dir / "assets" / "Source.icns"),
    bundle_identifier="com.kevinhouston.source",
    info_plist={
        "CFBundleDisplayName": "Source",
        "CFBundleName": "Source",
        "NSMicrophoneUsageDescription": "Source needs microphone access to record and transcribe local audio sessions.",
    },
)
