# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('BigShoulders-Bold.ttf', '.'), ('Poppins-Bold.ttf', '.'), ('dash_hud_bg.png', '.'), ('dash_hud2_bg.png', '.'), ('xrk_helper.py', '.'), ('ffmpeg.exe', '.')]
binaries = [('avcodec-62.dll', '.'), ('avformat-62.dll', '.'), ('avfilter-11.dll', '.'), ('avutil-60.dll', '.'), ('swscale-9.dll', '.'), ('swresample-6.dll', '.'), ('avdevice-62.dll', '.'), ('MatLabXRK-2022-64-ReleaseU.dll', '.'), ('libxml2-2.dll', '.'), ('libiconv-2.dll', '.'), ('libz.dll', '.'), ('pthreadVC2_x64.dll', '.'), ('msvcr90.dll', '.')]
hiddenimports = ['xrk_helper', 'xrk_reader', 'vbo_reader', 'renderer_pil', 'renderer_multistyle', 'lapdata_render', 'gtrace_render', 'trackmap_render', 'dash8_render', 'textgrid']
tmp_ret = collect_all('aggdraw')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['ecu_overlay_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['aim_reader', 'matplotlib'],
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
    name='LapStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
