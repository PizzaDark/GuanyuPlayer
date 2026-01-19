# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# 收集 vosk 的所有文件
vosk_datas = []
vosk_binaries = []

try:
    import vosk
    vosk_path = os.path.dirname(vosk.__file__)
    
    # 收集 DLL 文件
    for file in os.listdir(vosk_path):
        full_path = os.path.join(vosk_path, file)
        if file.endswith('.dll') or file.endswith('.so') or file.endswith('.dylib'):
            vosk_binaries.append((full_path, 'vosk'))
        elif os.path.isfile(full_path):
            vosk_datas.append((full_path, 'vosk'))
    
    print(f"Vosk path: {vosk_path}")
    print(f"Vosk binaries: {vosk_binaries}")
except ImportError:
    print("Vosk not installed, skipping...")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=vosk_binaries,
    datas=[
        ('guanyu_song.mp3', '.'),
        ('guanyu_song_1.mp3', '.'),
        ('guanyu_song_2.mp3', '.'),
        ('guanyu_song_3.mp3', '.'),
        ('guanyu_song_4.mp3', '.'),
        ('guanyu_icon.ico', '.'),
        # 语音模型（内置以便开箱即用）
        ('vosk-model-small-cn-0.22', 'vosk-model-small-cn-0.22'),
    ] + vosk_datas,
    hiddenimports=[
        'vosk',
        'pygame',
        'sounddevice',
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput._util',
        'pynput._util.win32',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'PIL', 'numpy.random._examples'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='关羽之歌便携版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 正式版设为 False，调试时设为 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='guanyu_icon.ico',
)