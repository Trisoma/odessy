"""Graphical entry point for launching a completed installation."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

from installer.core.checkpoint import read_state
from installer.core.preflight import REPO_ROOT
from installer.start import workspace_path
from installer.entrypoints import entry_command, independent_environment
from installer.ui import ui_theme as theme


def launch_console(root: Path, workspace: Path, *, popen=subprocess.Popen):
    if os.name != 'nt':
        raise ValueError('This launcher currently targets Windows.')
    workspace = workspace_path(root, workspace)
    from installer.operations.maintenance import require_idle
    require_idle(root)
    from installer.operations.uninstall import removal_started
    if removal_started(root):
        raise ValueError('This installation has been uninstalled and cannot be launched.')
    state = read_state(root)
    if state['status'] != 'completed':
        raise ValueError('Finish the installation before launching Odessy.')
    if Path(state['plan']['install_dir']).resolve() != root.resolve():
        raise ValueError('The installation directory has moved; it cannot be launched from here.')
    # Argument arrays preserve spaces, Unicode and shell metacharacters; no shell interpolation.
    return popen(entry_command('installer.start', ['--install-dir', str(root), '--workspace', str(workspace),
                  '--pause-on-exit']), cwd=Path(sys.executable).parent if getattr(sys, 'frozen', False) else REPO_ROOT,
                 env=independent_environment(), creationflags=subprocess.CREATE_NEW_CONSOLE)


class Launcher:
    def __init__(self, root, installation: Path, *, launch=launch_console):
        import tkinter as tk
        from tkinter import ttk, filedialog

        self.root = root
        self.installation = installation
        self.launch = launch
        self.process = None

        root.title('Odessy')
        root.geometry('760x600')
        root.minsize(700, 560)
        root.configure(background=theme.COLORS['bg'])
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        theme.configure(root)
        theme.header(root, title='Odessy', caption='Launcher')

        frame = ttk.Frame(root, padding=(32, 24, 32, 24))
        frame.grid(row=2, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)

        ttk.Label(frame, text='Start using Odessy', style='Title.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(frame, text='Choose the workspace folder Odessy is allowed to operate in.',
                  style='Hint.TLabel', wraplength=680).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(8, 6))
        ttk.Label(frame, text='Installation: ' + str(installation), style='Hint.TLabel',
                  wraplength=680).grid(row=2, column=0, columnspan=2, sticky='w', pady=(0, 22))

        ttk.Label(frame, text='Workspace folder').grid(row=3, column=0, sticky='w')
        self.workspace = tk.StringVar()
        self.message = tk.StringVar(value='The installation is checked and the local model service is prepared on launch.')
        self.entry = ttk.Entry(frame, textvariable=self.workspace)
        self.entry.grid(row=4, column=0, sticky='ew', pady=(6, 0))

        def browse():
            folder = filedialog.askdirectory(parent=root, title='Select workspace folder', mustexist=True)
            if folder:
                self.workspace.set(folder)

        self.browse = ttk.Button(frame, text='Browse…', command=browse)
        self.browse.grid(row=4, column=1, padx=(8, 0), pady=(6, 0))

        ttk.Label(frame, text=(
            'Requests are entered in a separate terminal; existing action confirmations still apply.\n'
            'Type /exit or press Ctrl+C to quit — only services started by this session are cleaned up.\n'
            'Closing this panel does not close a terminal that is already open.'
        ), style='Hint.TLabel', wraplength=680, justify='left').grid(
            row=5, column=0, columnspan=2, sticky='w', pady=(16, 8))

        ttk.Label(frame, textvariable=self.message, style='Hint.TLabel', wraplength=680).grid(
            row=6, column=0, columnspan=2, sticky='nw', pady=(8, 0))

        self.button = ttk.Button(frame, text='Open Odessy terminal', command=self.start, style='Primary.TButton')
        self.button.grid(row=7, column=0, columnspan=2, sticky='e', pady=(16, 0))

        self.callback = root.after(250, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        try:
            if not self.workspace.get().strip():
                raise ValueError('Select a workspace folder first.')
            self.process = self.launch(self.installation, Path(self.workspace.get()))
            self.message.set('Terminal opened. Enter your request there; '
                             'the model service is cleaned up when you exit.')
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.message.set(str(error)[:250])
        self.poll(schedule=False)

    def poll(self, *, schedule=True):
        active = self.process is not None and self.process.poll() is None
        self.button.configure(state='disabled' if active else 'normal')
        if self.process is not None and not active:
            self.message.set('Terminal closed. You can choose a workspace and launch again.')
            self.process = None
        if schedule:
            self.callback = self.root.after(250, self.poll)

    def close(self):
        # The separately opened console owns its session, independent of this panel.
        self.root.after_cancel(self.callback)
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
        root = tk.Tk()
    except ImportError as error:
        print('Tkinter is required by this development launcher:', error)
        return 1
    except tk.TclError as error:
        print('Cannot open graphical launcher:', error)
        return 1
    Launcher(root, args.install_dir)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
