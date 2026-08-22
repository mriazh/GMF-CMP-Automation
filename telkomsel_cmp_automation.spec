# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone Telkomsel CMP Automation portable build."""

import os
from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = os.path.abspath(SPECPATH)

block_cipher = None

hidden_imports = [
    "playwright",
    "playwright.async_api",
    "pydantic",
    "pydantic_settings",
    "openpyxl",
    "PIL",
    "dotenv",
    "python_dotenv",
    "zoneinfo",
]
hidden_imports += collect_submodules("playwright")
hidden_imports += collect_submodules("pydantic")
hidden_imports += collect_submodules("pydantic_settings")

datas = collect_data_files("playwright")

a = Analysis(
    ["src/cmp_automation/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_playwright", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

options = []

exe = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="Telkomsel-CMP-Automation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="Telkomsel-CMP-Automation",
)
