# PyInstaller spec for the Trip Review Tool: a one-folder Windows build so the
# reviewer gets app/engine code as bundled bytecode (no .py source shipped),
# not the raw source tree. Run with: pyinstaller --noconfirm --clean trip_review.spec

block_cipher = None

a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Flask templates + static assets: pure data, not auto-detected.
        # Destinations mirror the source layout (app/templates, app/static)
        # to match app.server._app_dir()'s sys._MEIPASS/app/... resolution.
        ('app/templates', 'app/templates'),
        ('app/static', 'app/static'),
    ],
    hiddenimports=[
        # pandas calls this dynamically (engine="openpyxl" string, never a
        # static `import openpyxl`), so PyInstaller's import scanner misses it.
        'openpyxl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev-only deps: never imported by launch.py's import graph, excluded
        # explicitly to keep the build lean. matplotlib + PIL are NOT excluded:
        # app/charts.py renders the export's start-time PNGs with matplotlib
        # (Agg), and matplotlib >= 3.3 writes PNGs through Pillow. tkinter
        # stays excluded -- the Agg backend never touches it.
        'pytest',
        'tkinter',
        'IPython',
        'scipy',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TripReview',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep the console: launch.py prints the URL, Ctrl+C stops it
    # UiTR lab logo as the exe resource: Explorer, taskbar, and the console
    # window all read their icon from here (replaces the default PyInstaller
    # python icon).
    icon='app/static/favicon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='TripReview',
)
