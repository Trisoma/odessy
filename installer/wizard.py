"""Desktop installation wizard for Odessy — Windows x64."""
from __future__ import annotations
import argparse
import os
import queue
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from installer.checkpoint import fingerprint, state_path
from installer.preflight import default_locations, REPO_ROOT
from installer.wizard_controller import Controller, Options
from installer.entrypoints import entry_command, independent_environment
from installer import ui_theme as theme

STAGES = {
    'tool_bundle': 'Resource verification',
    'runtime': 'Python & Node runtime',
    'dependencies': 'Application dependencies',
    'application': 'Application files',
    'ollama_runtime': 'Local model service',
    'model': 'Model files',
    'connection': 'Real-model & tool validation',
    'configuration': 'Connection configuration',
}
STATUS = {
    'pending': 'Waiting',
    'running': 'In progress',
    'completed': 'Completed',
    'failed': 'Failed',
    'cancelled': 'Stopped',
}


class Wizard:
    def __init__(self, root, options: Options, *, controller=None):
        import tkinter as tk
        from tkinter import ttk, filedialog
        self.tk = tk; self.ttk = ttk; self.filedialog = filedialog
        self.root = root
        self.controller = controller or Controller()
        self.page = 0
        self.active = False
        self.close_pending = False
        self.preview = None
        self.resume = options.resume
        self.outcome = None
        self.installed_root = None

        self.fields = {name: tk.StringVar(value=value)
                       for name, value in asdict(options).items() if name != 'resume'}
        self.fields['install_dir'].trace_add('write', self._path_changed)
        self.error = tk.StringVar()
        self.mode = tk.StringVar(value='New installation')
        self.current = tk.StringVar(value='Ready to check installation plan')
        self.count = tk.StringVar(value='0 of 8 stages completed')

        self.root.title('Odessy Setup')
        self.root.geometry('900x680')
        self.root.minsize(820, 600)
        self.root.configure(background=theme.COLORS['bg'])
        self.root.protocol('WM_DELETE_WINDOW', self.close)

        theme.configure(root)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # ── Header ──────────────────────────────────────────────────────────
        theme.header(root, title='Odessy', caption='Windows x64  ·  Setup')

        # ── Step track ──────────────────────────────────────────────────────
        self._stepper = theme.Stepper(root, ('Settings', 'Review', 'Progress'), row=1)

        # ── Content area ────────────────────────────────────────────────────
        outer = ttk.Frame(root, padding=(36, 24, 36, 0))
        outer.grid(row=2, column=0, sticky='nsew')
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        self.heading = ttk.Label(outer, text='Installation settings', style='Title.TLabel')
        self.heading.grid(row=0, column=0, sticky='w')

        self.subtitle = ttk.Label(outer, text='Review the plan before downloading, configuring and validating.',
                                  style='Hint.TLabel', wraplength=780)
        self.subtitle.grid(row=1, column=0, sticky='w', pady=(6, 18))

        self.stack = ttk.Frame(outer)
        self.stack.grid(row=2, column=0, sticky='nsew')
        self.stack.columnconfigure(0, weight=1)
        self.stack.rowconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        self.pages = [ttk.Frame(self.stack) for _ in range(3)]
        for page in self.pages:
            page.grid(row=0, column=0, sticky='nsew')

        self.inputs = []
        self._setup_page()
        self._review_page()
        self._progress_page()

        # ── Footer ──────────────────────────────────────────────────────────
        footer = ttk.Frame(root, padding=(36, 12, 36, 20))
        footer.grid(row=3, column=0, sticky='ew')
        footer.columnconfigure(1, weight=1)

        self.back_button = ttk.Button(footer, text='← Back', command=lambda: self.show(0))
        self.back_button.grid(row=0, column=0)

        self.close_button = ttk.Button(footer, text='Close', command=self.close)
        self.close_button.grid(row=0, column=2, padx=(8, 0))

        self.primary = ttk.Button(footer, text='Check installation plan',
                                  style='Primary.TButton', command=self.primary_action)
        self.primary.grid(row=0, column=3, padx=(8, 0))

        self.show(0)
        self.root.after(60, self.poll)

    # ── Field helpers ────────────────────────────────────────────────────────

    def _path_row(self, parent, row, label, field):
        ttk = self.ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, columnspan=2, sticky='w', pady=(10, 4))
        entry = ttk.Entry(parent, textvariable=self.fields[field])
        entry.grid(row=row + 1, column=0, sticky='ew')
        button = ttk.Button(parent, text='Browse…', command=lambda: self.browse(field))
        button.grid(row=row + 1, column=1, padx=(8, 0))
        self.inputs.extend((entry, button))

    # ── Page 0: Settings ─────────────────────────────────────────────────────

    def _setup_page(self):
        ttk = self.ttk
        container = self.pages[0]
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        canvas = self.tk.Canvas(container, background=theme.COLORS['bg'], highlightthickness=0)
        canvas.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(container, orient='vertical', command=canvas.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        canvas.configure(yscrollcommand=scroll.set)

        page = ttk.Frame(canvas)
        page.columnconfigure(0, weight=1)
        cw = canvas.create_window((0, 0), window=page, anchor='nw')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(cw, width=e.width))
        page.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        self.setup_canvas = canvas

        self._path_row(page, 0, 'Installation directory', 'install_dir')
        ttk.Label(page, text='Choose a new or empty folder. An existing installation record in this folder will be detected.',
                  style='Hint.TLabel', wraplength=750).grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 14))

        if getattr(sys, 'frozen', False):
            ttk.Label(page, text='Application resources are bundled — no need to select a source directory.',
                      style='Hint.TLabel', wraplength=750).grid(row=3, column=0, columnspan=2, sticky='w', pady=(0, 10))
        else:
            self._path_row(page, 3, 'Installation resource directory', 'tool_bundle')
            ttk.Label(page, text='Source entry uses locally built resources. The EXE build bundles them automatically.',
                      style='Hint.TLabel', wraplength=750).grid(row=5, column=0, columnspan=2, sticky='w', pady=(4, 10))

        self.advanced = self.tk.BooleanVar(value=False)
        toggle = ttk.Checkbutton(page, text='Advanced settings', variable=self.advanced,
                                 command=self.toggle_advanced)
        toggle.grid(row=6, column=0, sticky='w', pady=(8, 0))
        self.inputs.append(toggle)

        self.extra = ttk.Frame(page)
        self.extra.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(8, 4))
        self.extra.columnconfigure(0, weight=1)
        self._path_row(self.extra, 0, 'Download cache directory', 'cache_dir')
        opts = ttk.Frame(self.extra)
        opts.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        opts.columnconfigure(0, weight=1)
        ttk.Label(opts, text='Local model service URL').grid(row=0, column=0, sticky='w')
        ttk.Label(opts, text='Call timeout (seconds)').grid(row=0, column=1, sticky='w', padx=(12, 0))
        for col, field in enumerate(('endpoint', 'timeout')):
            e = ttk.Entry(opts, textvariable=self.fields[field], width=22 if col == 0 else 14)
            e.grid(row=1, column=col, sticky='ew', padx=(0 if col == 0 else 12, 0), pady=(4, 0))
            self.inputs.append(e)
        self.extra.grid_remove()

        # Record / mode actions
        actions = ttk.Frame(page)
        actions.grid(row=8, column=0, columnspan=2, sticky='w', pady=(16, 4))
        load = ttk.Button(actions, text='Load installation record', command=self.load_record)
        load.pack(side='left')
        fresh = ttk.Button(actions, text='Use new installation mode', command=self.new_mode)
        fresh.pack(side='left', padx=8)
        self.inputs.extend((load, fresh))

        ttk.Label(page, textvariable=self.mode, style='Hint.TLabel').grid(
            row=9, column=0, columnspan=2, sticky='w', pady=(4, 4))
        ttk.Label(page, textvariable=self.error, style='Error.TLabel', wraplength=750).grid(
            row=10, column=0, columnspan=2, sticky='w')

        # Separator + maintenance actions (low emphasis)
        ttk.Separator(page).grid(row=11, column=0, columnspan=2, sticky='ew', pady=(18, 10))
        maint = ttk.Frame(page)
        maint.grid(row=12, column=0, columnspan=2, sticky='w', pady=(0, 4))
        diag = theme.ghost_button(maint, 'View diagnostic log', self.show_logs)
        diag.pack(side='left')
        unin = theme.ghost_button(maint, 'Uninstall / clean this directory…', self.open_uninstaller)
        unin.pack(side='left', padx=(10, 0))
        rep = theme.ghost_button(maint, 'Repair / upgrade this directory…', self.open_maintenance)
        rep.pack(side='left', padx=(10, 0))
        self.inputs.extend((diag, unin, rep))

    # ── Page 1: Review ───────────────────────────────────────────────────────

    def _review_page(self):
        ttk = self.ttk
        page = self.pages[1]
        page.columnconfigure(0, weight=1)

        self.review_text = self.tk.StringVar()
        ttk.Label(page, textvariable=self.review_text, wraplength=760, justify='left').grid(
            row=0, column=0, sticky='nw', pady=(4, 16))

        self.space = self.tk.StringVar()
        ttk.Label(page, textvariable=self.space, wraplength=760, justify='left').grid(
            row=1, column=0, sticky='w', pady=8)

        ttk.Separator(page).grid(row=2, column=0, sticky='ew', pady=18)

        ttk.Label(page, text=(
            'The installer will prepare the runtime, application and local model, '
            'then perform two live inference calls to verify the setup.\n'
            'Existing validated components are reused — no other installation directories are touched.'
        ), wraplength=760).grid(row=3, column=0, sticky='w', pady=6)

        ttk.Label(page, text=(
            'After verification the model service started during this session is stopped; '
            'existing services are left running. '
            'A launcher panel and shortcuts are available once installation completes.'
        ), style='Hint.TLabel', wraplength=760).grid(row=4, column=0, sticky='w', pady=12)

        self.review_notice = self.tk.StringVar()
        ttk.Label(page, textvariable=self.review_notice, style='Hint.TLabel', wraplength=760).grid(
            row=5, column=0, sticky='w', pady=8)

    # ── Page 2: Progress ─────────────────────────────────────────────────────

    def _progress_page(self):
        ttk = self.ttk
        page = self.pages[2]
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=1)

        ttk.Label(page, textvariable=self.current, wraplength=760).grid(
            row=0, column=0, sticky='w', pady=(4, 8))
        self.bar = ttk.Progressbar(page, maximum=8, mode='determinate', style='Horizontal.TProgressbar')
        self.bar.grid(row=1, column=0, sticky='ew')
        ttk.Label(page, textvariable=self.count, style='Hint.TLabel').grid(
            row=2, column=0, sticky='w', pady=(6, 12))

        self.tabs = ttk.Notebook(page)
        self.tabs.grid(row=3, column=0, sticky='nsew')
        stage_page = ttk.Frame(self.tabs)
        log_page = ttk.Frame(self.tabs)
        self.tabs.add(stage_page, text='Stages')
        self.tabs.add(log_page, text='Diagnostic log')

        self.tree = ttk.Treeview(stage_page, columns=('name', 'status'), show='headings',
                                 height=8, selectmode='none')
        self.tree.heading('name', text='Stage')
        self.tree.heading('status', text='Status')
        self.tree.column('name', width=480, stretch=True)
        self.tree.column('status', width=160, stretch=False)
        self.tree.pack(fill='both', expand=True)
        for name, title in STAGES.items():
            self.tree.insert('', 'end', iid=name, values=(title, 'Waiting'))
        self.tree.tag_configure('completed', foreground=theme.COLORS['success'])
        self.tree.tag_configure('failed', foreground=theme.COLORS['danger'])
        self.tree.tag_configure('running', foreground=theme.COLORS['accent'])

        tk = self.tk
        self.log = tk.Text(log_page, wrap='word', background=theme.COLORS['surface'],
                           foreground=theme.COLORS['text'], insertbackground=theme.COLORS['text'],
                           font=('Consolas', 10), relief='flat', state='disabled',
                           padx=12, pady=8)
        log_scroll = ttk.Scrollbar(log_page, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)

        self.result_note = tk.StringVar()
        ttk.Label(page, textvariable=self.result_note, style='Hint.TLabel', wraplength=760).grid(
            row=4, column=0, sticky='w', pady=(10, 0))

        shortcuts = ttk.Frame(page)
        shortcuts.grid(row=5, column=0, sticky='w', pady=(10, 0))
        self.shortcut_desktop = tk.BooleanVar(value=True)
        self.shortcut_menu = tk.BooleanVar(value=True)
        self.shortcut_choices = [
            ttk.Checkbutton(shortcuts, text='Desktop shortcut', variable=self.shortcut_desktop),
            ttk.Checkbutton(shortcuts, text='Start menu shortcut', variable=self.shortcut_menu),
        ]
        for i, w in enumerate(self.shortcut_choices):
            w.grid(row=0, column=i, padx=(0, 14))
        self.shortcut_button = ttk.Button(shortcuts, text='Create / restore shortcuts',
                                          command=self.create_shortcuts)
        self.shortcut_button.grid(row=0, column=2)

    # ── Navigation ───────────────────────────────────────────────────────────

    def show(self, index):
        self.page = index
        self._stepper.set(index)
        titles = ('Installation settings', 'Review plan', 'Installation progress')
        subtitles = (
            'Review the plan before downloading, configuring and validating.',
            'Confirm the plan below, then start the installation.',
            'Monitor progress and review the diagnostic log.',
        )
        self.heading.configure(text=titles[index])
        self.subtitle.configure(text=subtitles[index])
        for i, page in enumerate(self.pages):
            if i == index:
                page.tkraise()
        self.sync_controls()

    def sync_controls(self):
        self.back_button.configure(state='normal' if self.page == 1 else 'disabled')

        for widget in self.inputs:
            try:
                widget.configure(state='disabled' if self.active else 'normal')
            except Exception:
                pass

        if self.active:
            self.primary.configure(text='Stop', style='Secondary.TButton', state='normal')
        elif self.page == 0:
            self.primary.configure(text='Check installation plan', style='Primary.TButton', state='normal')
            self.error.set('')
        elif self.page == 1:
            can_start = bool(self.preview) and self.preview.get('sufficient', False)
            if self.resume:
                self.primary.configure(text='Resume installation', style='Primary.TButton',
                                       state='normal' if can_start else 'disabled')
            else:
                self.primary.configure(text='Start installation', style='Primary.TButton',
                                       state='normal' if can_start else 'disabled')
        elif self.page == 2:
            if self.outcome == 'completed':
                self.primary.configure(text='Open Odessy', style='Primary.TButton', state='normal')
            else:
                self.primary.configure(text='Check plan and retry', style='Secondary.TButton',
                                       state='normal' if self.preview else 'disabled')

        shortcut_active = (self.outcome == 'completed' and not self.active
                           and self.installed_root is not None)
        self.shortcut_button.configure(state='normal' if shortcut_active else 'disabled')

    def primary_action(self):
        if self.active:
            self.stop(); return
        if self.page == 0:
            if self.preview and not self._options_changed():
                self.show(1); return
            self.begin('preview', self.options())
        elif self.page == 1:
            if not self.preview or self._options_changed():
                self.show(0); return
            if not self.preview.get('sufficient', False):
                return
            self.begin('execute', self.preview)
        elif self.page == 2:
            if self.outcome == 'completed':
                self._open_launcher()
            elif self.preview:
                self.fill_options({**self.preview['options'],
                                   'resume': state_path(Path(self.preview['plan']['install_dir'])).is_file()})
                self.show(0)
                self.begin('preview', self.options())
            else:
                self.show(0)

    def _options_changed(self):
        if self.preview is None:
            return True
        prev = self.preview.get('options_sha256', '')
        return fingerprint(asdict(self.options())) != prev

    def _open_launcher(self):
        if self.installed_root is None:
            return
        try:
            subprocess.Popen(
                entry_command('installer.launcher', ['--install-dir', str(self.installed_root)]),
                cwd=Path(sys.executable).parent if getattr(sys, 'frozen', False) else REPO_ROOT,
                env=independent_environment(),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as err:
            self.error.set(str(err)[:300])

    def begin(self, job, payload):
        self.active = True
        self.controller.start(job, payload)
        self.sync_controls()

    # ── Field helpers ────────────────────────────────────────────────────────

    def options(self):
        return Options(**{name: var.get() for name, var in self.fields.items()}, resume=self.resume)

    def _path_changed(self, *_args):
        self.resume = False
        if hasattr(self, 'mode'):
            self.mode.set('New installation')
        self.preview = None

    def fill_options(self, options):
        for name, value in options.items():
            if name != 'resume':
                self.fields[name].set(value)
        self.resume = options['resume']
        self.mode.set('Resuming saved installation' if self.resume else 'New installation')
        self.preview = None

    def browse(self, field):
        path = self.filedialog.askdirectory(parent=self.root, title='Select folder', mustexist=False)
        if path:
            self.fields[field].set(path)

    def toggle_advanced(self):
        if self.advanced.get():
            self.extra.grid()
        else:
            self.extra.grid_remove()

    def load_record(self):
        if self.active:
            return
        self.error.set('')
        self.begin('record', self.options())

    def new_mode(self):
        if self.active:
            return
        self.resume = False
        self.mode.set('New installation')
        self.preview = None
        self.error.set('')

    def show_logs(self):
        self.show(2)
        self.tabs.select(1)

    def open_maintenance(self):
        if self.active:
            return
        try:
            root = Path(self.fields['install_dir'].get().strip())
            if not root.is_absolute():
                raise ValueError('Please select an absolute path to an existing installation.')
            if not getattr(sys, 'frozen', False):
                self.error.set('Use the standalone EXE to open the repair/upgrade window.')
                return
            subprocess.Popen(
                entry_command('installer.maintenance_ui', ['--install-dir', str(root)]),
                cwd=Path(sys.executable).parent,
                env=independent_environment(),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, ValueError) as err:
            self.error.set(str(err)[:300])

    def open_uninstaller(self):
        if self.active:
            return
        try:
            root = Path(self.fields['install_dir'].get().strip())
            if not root.is_absolute():
                raise ValueError('Please select an absolute path to an existing installation.')
            subprocess.Popen(
                entry_command('installer.uninstall_ui', ['--install-dir', str(root)]),
                cwd=Path(sys.executable).parent if getattr(sys, 'frozen', False) else REPO_ROOT,
                env=independent_environment(),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, ValueError) as err:
            self.error.set(str(err)[:300])

    def create_shortcuts(self):
        if self.active or self.outcome != 'completed' or self.installed_root is None:
            return
        if not getattr(sys, 'frozen', False):
            self.result_note.set('Use the standalone EXE to create launcher shortcuts.')
            return
        if not self.shortcut_desktop.get() and not self.shortcut_menu.get():
            self.result_note.set('Select at least one shortcut location (desktop or Start menu).')
            return
        self.result_note.set('Creating shortcuts…')
        source = self.installed_root / 'launcher/OdessyLauncher.exe'
        if not source.is_file():
            source = Path(sys.executable)
        self.begin('shortcuts', dict(
            install_dir=str(self.installed_root),
            executable=str(source),
            desktop=self.shortcut_desktop.get(),
            start_menu=self.shortcut_menu.get(),
        ))

    # ── Log helpers ──────────────────────────────────────────────────────────

    def append_log(self, text):
        log = self.log
        log.configure(state='normal')
        log.insert('end', text + '\n')
        log.see('end')
        log.configure(state='disabled')

    # ── Message handling ─────────────────────────────────────────────────────

    def handle(self, message):
        kind = message['kind']

        if kind == 'log':
            self.append_log(message['text']); return

        if kind == 'progress':
            event = message['event']
            step, status = event.get('step'), event.get('status')
            if step in STAGES:
                self.tree.item(step, values=(STAGES[step], STATUS.get(status, status)),
                               tags=(status,))
            if event.get('status') == 'running':
                self.current.set(f'Installing: {STAGES.get(step, step)}…')
            completed = event.get('completed_steps', 0)
            self.bar['value'] = completed
            self.count.set(f'{completed} of 8 stages completed')
            self.active = not self.controller.messages.empty() or self.controller.busy
            self.sync_controls(); return

        self.active = False

        if kind == 'error':
            self.error.set(message['message'][:320])
            self.append_log(message.get('error', message['message']))
            if message['job'] in ('execute', 'shortcuts'):
                self.outcome = 'failed' if message['job'] == 'execute' else self.outcome
                self.current.set('Installation did not complete')
                self.result_note.set(message['message'][:400])
                self.show(2)
            else:
                self.show(0)

        elif kind == 'cancelled':
            self.outcome = 'cancelled'
            self.current.set('Stopped safely')
            self.result_note.set(
                'Completed components and checkpoints have been preserved. '
                'You can review and resume the plan.')
            if message['job'] == 'execute':
                self.show(2)

        elif kind == 'finished':
            result = message['result']
            if message['job'] == 'preview':
                self.preview = result
                plan = result['plan']
                self.review_text.set(
                    f'Mode: {"Resume and re-validate" if result["resume"] else "New installation"}\n\n'
                    f'Installation directory: {plan["install_dir"]}\n\n'
                    f'Cache directory: {plan["cache_dir"]}\n\n'
                    f'Local model: {plan["model"]}'
                )
                lines = []
                for vol in result['volumes']:
                    kinds = ' / '.join(
                        'cache' if k == 'cache' else 'install'
                        for k in vol['contributions'] if k != 'models'
                    )
                    lines.append(
                        f'{kinds} disk: requires {vol["required_bytes"] / 1024**3:.2f} GiB, '
                        f'{vol["free_bytes"] / 1024**3:.2f} GiB available'
                    )
                self.space.set(
                    '\n'.join(lines) +
                    '\n\nBudget includes cache and temporary files; actual downloads depend on cache hits.'
                )
                if result['sufficient']:
                    self.review_notice.set('Disk space check passed — ready to install.')
                else:
                    self.review_notice.set(
                        'Insufficient disk space. Go back and adjust the installation or cache directory.')
                self.show(1)

            elif message['job'] == 'record':
                self.fill_options(result['options'])
                state = result['state']
                self.outcome = None
                if state['status'] == 'completed':
                    self.outcome = 'completed'
                    self.installed_root = Path(result['options']['install_dir'])
                self.show(2)
                self.current.set('Installation record loaded: ' + STATUS.get(state['status'], state['status']))
                completed = sum(s['status'] == 'completed' for s in state['steps'])
                self.bar['value'] = completed
                self.count.set(f'{completed} of 8 stages completed')
                for step in state['steps']:
                    if step['name'] in STAGES:
                        self.tree.item(step['name'],
                                       values=(STAGES[step['name']], STATUS.get(step['status'], step['status'])),
                                       tags=(step['status'],))
                    if step.get('error'):
                        self.append_log(step['name'] + ': ' + step['error'])
                self.result_note.set(
                    'Record loaded for review. Return to settings to check a resume plan.')

            elif message['job'] == 'shortcuts':
                if kind == 'error':
                    self.result_note.set('Shortcuts were not created — the installation is preserved. '
                                         + message.get('message', 'See the diagnostic log.'))
                    self.append_log(message.get('error', 'Shortcut creation did not complete.'))
                else:
                    self.result_note.set('Shortcuts are ready. '
                                         'You can now open Odessy from the desktop or Start menu.')
                    self.append_log('Persistent launcher: ' + message['result']['executable'])

            else:
                if (result.get('status') != 'completed'
                        or not result.get('summary', {}).get('workflow_complete')):
                    self.outcome = 'failed'
                    self.current.set('Process did not confirm completion — check the diagnostic log.')
                else:
                    self.outcome = 'completed'
                    self.installed_root = Path(self.preview['plan']['install_dir'])
                    self.bar['value'] = 8
                    self.count.set('8 of 8 stages completed')
                    self.current.set('Installation and live validation complete')
                    self.result_note.set(
                        'Connection configuration saved. The model service started during this session has been stopped; '
                        'pre-existing services are unchanged. Click "Open Odessy" to choose a workspace folder.')
                self.show(2)

        self.sync_controls()

    def poll(self):
        for _ in range(180):
            try:
                message = self.controller.messages.get_nowait()
            except queue.Empty:
                break
            self.handle(message)
        if self.close_pending and not self.active:
            self.root.destroy(); return
        self.sync_controls()
        self.root.after(60, self.poll)

    def stop(self):
        if self.active:
            self.controller.request_cancel()
            self.current.set('Stopping safely — waiting for the current step to finish…')
            self.result_note.set(
                'Completed components and checkpoints will be preserved. '
                'You can resume once the current call finishes.')
            self.sync_controls()

    def close(self):
        if self.active:
            self.close_pending = True
            self.controller.request_cancel()
            self.current.set('Stopping safely — keep this window open until the current step finishes…')
            self.result_note.set(
                'No services will be forcibly terminated. '
                'A live model call may take until its timeout before the window closes.')
            self.show(2)
            self.sync_controls()
        else:
            self.root.destroy()


def main(argv=None):
    app, _ = default_locations(os.environ, Path.home())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=app / 'installation')
    parser.add_argument('--cache-dir', type=Path, default=app.parent.parent / 'Odessy/downloads')
    parser.add_argument('--tool-bundle', type=Path)
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as err:
            print('Cannot open installer window:', err)
            return 1
    except (ImportError, RuntimeError) as err:
        print('This entry point requires Tkinter/Tcl. Use the EXE build or a Python with Tkinter:', err)
        return 1
    Wizard(root, Options(str(args.install_dir), str(args.cache_dir), str(args.tool_bundle or '')))
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
