# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

chroma_datas, chroma_binaries, chroma_hiddenimports = collect_all('chromadb')
onnx_datas, onnx_binaries, onnx_hiddenimports = collect_all('onnxruntime')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=chroma_binaries + onnx_binaries,
    datas=chroma_datas + onnx_datas,
    hiddenimports=chroma_hiddenimports + onnx_hiddenimports + [
        'chromadb.utils.embedding_functions',
        'onnxruntime',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='jarvis-agent-x86_64-unknown-linux-gnu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
