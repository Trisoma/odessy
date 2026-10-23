"""Single-file executable dispatch. Imported modules keep the source CLI usable."""
from __future__ import annotations
import argparse
import json
import os
import sys
import traceback
import signal
from pathlib import Path
from installer.entrypoints import independent_environment


def console():
    if not getattr(sys, 'frozen', False): return
    import ctypes
    kernel = ctypes.windll.kernel32
    if not kernel.GetConsoleWindow():
        if not kernel.AllocConsole(): raise ctypes.WinError()
    signal.signal(signal.SIGINT, signal.default_int_handler)
    kernel.SetConsoleCP(65001); kernel.SetConsoleOutputCP(65001)
    sys.stdin = open('CONIN$', 'r', encoding='utf-8')
    sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
    sys.stderr = open('CONOUT$', 'w', encoding='utf-8', buffering=1)


def prepare_frozen():
    if not getattr(sys, 'frozen', False): return
    # Resolve bundled extension DLL dependencies before sanitizing the search path
    # inherited by private Python, Node and Ollama processes launched later.
    import tkinter, ssl, lzma, bz2, ctypes
    if os.name == 'nt': ctypes.windll.kernel32.SetDllDirectoryW(None)


def installed_launcher_root():
    executable=Path(sys.executable)
    if (getattr(sys,'frozen',False) and executable.name.lower()=='odessylauncher.exe'
        and executable.parent.name=='launcher' and (executable.parent/'odessy-shortcuts.json').is_file()):
        return executable.parent.parent
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--mode', choices=('wizard', 'launcher', 'start', 'self-check', 'uninstall', 'maintenance'), default='launcher' if installed_launcher_root() else 'wizard')
    mode, remaining = parser.parse_known_args(argv)
    if mode.mode == 'launcher' and installed_launcher_root() and '--install-dir' not in remaining:
        remaining += ['--install-dir', str(installed_launcher_root())]
    prepare_frozen()
    if mode.mode == 'start':
        console()
        from installer.start import main as run
        return run(remaining)
    if mode.mode == 'maintenance':
        from installer.maintenance_ui import main as run
        return run(remaining)
    if mode.mode == 'uninstall':
        from installer.uninstall_ui import main as run
        return run(remaining)
    from installer.core.preflight import REPO_ROOT, default_locations
    from installer.windows.access import is_elevated
    from installer.build.bootstrap import bundled_tools
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    if mode.mode == 'self-check':
        check = argparse.ArgumentParser(); check.add_argument('--report', type=Path, required=True)
        args = check.parse_args(remaining)
        import tkinter as tk
        from installer.components.application import source_snapshot, validate_bundle
        from installer.components.toolbundle import source_files
        from installer import workflow
        root = tk.Tk(); root.withdraw()
        try:
            tool = bundled_tools(REPO_ROOT, local)
            plan = workflow.build_plan(local / 'Programs/Odessy/bootstrap-check', local / 'Odessy/downloads', tool,
                                       'http://127.0.0.1:11435', 180)
            result = dict(frozen=bool(getattr(sys, 'frozen', False)), executable=sys.executable, elevated=is_elevated(),
                          tkinter_version=root.tk.call('info', 'patchlevel'), application_files=len(source_snapshot(REPO_ROOT)),
                          tool_source_files=len(source_files(REPO_ROOT)), release_sha256=plan['release_sha256'],
                          tool_bundle=str(tool), installation_started=False)
            args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
            return 0
        finally: root.destroy()
    if mode.mode == 'launcher':
        from installer.launcher import main as run
    else:
        if getattr(sys, 'frozen', False) and '--tool-bundle' not in remaining:
            remaining += ['--tool-bundle', str(bundled_tools(REPO_ROOT, local))]
        from installer.wizard import main as run
    return run(remaining)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        if getattr(sys, 'frozen', False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error)[:1200], 'Odessy failed to start', 0x10)
        else: traceback.print_exc()
        raise SystemExit(1)
