"""Graphical repair/upgrade window with explicit rollback and backup cleanup."""
from __future__ import annotations
import argparse
import os
import queue
import sys
import threading
from pathlib import Path

from installer.operations import maintenance

from installer.operations import component_repair

from installer.operations import backup_cleanup
from installer.ui import ui_theme as theme


class Maintenance:
    def __init__(self, root, installation, bundle, executable, *,
                 preview=component_repair.plan, apply=component_repair.execute,
                 recover=maintenance.recover, cleanup_preview=backup_cleanup.plan,
                 cleanup=backup_cleanup.execute, confirm=None):
        import tkinter as tk
        from tkinter import ttk, messagebox

        self.root = root
        self.installation = installation
        self.bundle = bundle
        self.executable = executable
        self.preview_function = preview
        self.apply_function = apply
        self.recover_function = recover
        self.confirm = confirm or (
            lambda text: messagebox.askyesno('Confirm maintenance', text, parent=root, default='no'))
        self.cleanup_preview_function = cleanup_preview
        self.cleanup_function = cleanup
        self.cleanup_plan = None
        self.plan = None
        self.busy = False
        self.close_pending = False
        self.thread = None
        self.messages = queue.Queue()

        root.title('Repair and upgrade Odessy')
        root.geometry('860x680')
        root.minsize(760, 600)
        root.configure(background=theme.COLORS['bg'])
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        theme.configure(root)
        theme.header(root, title='Odessy', caption='Maintenance')

        body = ttk.Frame(root, padding=(32, 22, 32, 0))
        body.grid(row=2, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        ttk.Label(body, text='Repair and upgrade', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(body, text='Installation: ' + str(installation), style='Hint.TLabel').grid(
            row=1, column=0, sticky='w', pady=(6, 4))
        ttk.Label(body, text=(
            'Detects and repairs private components, the application and launcher entries. '
            'Healthy models are reused; configuration and workspaces are preserved. '
            'Close all windows for this installation first.'
        ), style='Hint.TLabel', wraplength=780).grid(row=2, column=0, sticky='w', pady=(0, 12))

        tk_ = tk
        self.text = tk_.Text(body, height=17, wrap='word', state='disabled',
                             background=theme.COLORS['surface'], foreground=theme.COLORS['text'],
                             relief='flat', borderwidth=0, padx=16, pady=12,
                             font=(theme.FONT, 10),
                             highlightthickness=1,
                             highlightbackground=theme.COLORS['border'],
                             highlightcolor=theme.COLORS['border'])
        self.text.grid(row=3, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        scroll.grid(row=3, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scroll.set)

        self.note = tk_.StringVar(value='Check the plan first, then confirm.')
        ttk.Label(body, textvariable=self.note, style='Hint.TLabel', wraplength=780).grid(
            row=4, column=0, columnspan=2, sticky='w', pady=12)

        buttons = ttk.Frame(root, padding=(32, 0, 32, 20))
        buttons.grid(row=3, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)

        utils = ttk.Frame(buttons)
        utils.grid(row=0, column=0, sticky='w')
        self.cleanup_preview_button = ttk.Button(utils, text='Review backup usage…',
                                                 command=self.preview_cleanup)
        self.cleanup_preview_button.pack(side='left')
        self.cleanup_button = ttk.Button(utils, text='Clean up backups',
                                         command=self.cleanup, state='disabled')
        self.cleanup_button.pack(side='left', padx=(8, 0))

        actions = ttk.Frame(buttons)
        actions.grid(row=0, column=2, sticky='e')
        self.rollback_button = ttk.Button(actions, text='Restore previous version',
                                          command=self.rollback)
        self.rollback_button.pack(side='left', padx=(0, 8))
        self.preview_button = ttk.Button(actions, text='Check plan', command=self.preview)
        self.preview_button.pack(side='left', padx=(0, 8))
        self.apply_button = ttk.Button(actions, text='Confirm repair / upgrade',
                                       command=self.apply, state='disabled', style='Primary.TButton')
        self.apply_button.pack(side='left')

        self.callback = root.after(60, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def show(self, text):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')

    def begin(self, job, action):
        if self.busy:
            return
        self.busy = True
        for button in (self.preview_button, self.apply_button, self.rollback_button,
                       self.cleanup_preview_button, self.cleanup_button):
            button.configure(state='disabled')
        self.note.set('Checking — please wait…' if job == 'preview' else 'Maintaining, verifying, please wait…')

        def work():
            try:
                self.messages.put((job, action(), None))
            except Exception as err:
                self.messages.put((job, None, str(err)))

        self.thread = threading.Thread(target=work, name='odessy-maintenance', daemon=False)
        self.thread.start()

    # ── Actions ──────────────────────────────────────────────────────────────

    def preview(self):
        self.cleanup_plan = None
        self.plan = None
        self.begin('preview', lambda: self.preview_function(self.installation, self.bundle, self.executable))

    def apply(self):
        if self.busy or self.plan is None:
            return
        plan = self.plan
        components = ', '.join(plan.get('repair_components', [])) or 'none — base components reused'
        if not self.confirm('The new application and launcher entries will be prepared and verified '
                            'before switching this installation:\n' + str(self.installation) +
                            '\n\nComponents to repair: ' + components +
                            '\nConfiguration and workspaces are preserved; '
                            'old components are kept as a rollback backup.\n\nContinue?'):
            return
        self.begin('apply', lambda: self.apply_function(
            self.installation, self.bundle, self.executable, plan['plan_sha256']))

    def rollback(self):
        if self.busy:
            return
        if not self.confirm('This restores the application and launcher entries from before the most '
                            'recent maintenance. Files changed by the repair or update are moved to a '
                            'retained folder; configuration stays in place.\n\nContinue?'):
            return
        self.begin('rollback', lambda: self.recover_function(self.installation))

    def preview_cleanup(self):
        if self.busy:
            return
        self.plan = None
        self.cleanup_plan = None
        self.begin('cleanup_preview', lambda: self.cleanup_preview_function(self.installation))

    def cleanup(self):
        if self.busy or self.cleanup_plan is None:
            return
        plan = self.cleanup_plan
        if not self.confirm('This permanently deletes the rollback backup from the most recent '
                            'maintenance:\n' + str(self.installation) +
                            '\nTotal size: ' + f"{plan['bytes_to_remove'] / 1024**2:.1f} MiB" +
                            '\nYou will not be able to restore the version from before that maintenance. '
                            'Application, models, configuration and workspaces are kept.\n\nContinue?'):
            return
        self.begin('cleanup', lambda: self.cleanup_function(self.installation, plan['plan_sha256']))

    # ── Message pump ─────────────────────────────────────────────────────────

    def poll(self):
        try:
            job, result, error = self.messages.get_nowait()
            self.busy = False
            self.preview_button.configure(state='normal')
            self.rollback_button.configure(state='normal')
            self.cleanup_preview_button.configure(state='normal')

            if error:
                self.plan = None
                self.cleanup_plan = None
                self.show(error)
                if job == 'cleanup':
                    self.note.set('Backup cleanup did not finish. Review the scope again; '
                                  'rollback is no longer available if deletion has started.')
                else:
                    self.note.set('Maintenance did not complete. '
                                  'If prompted to restore the previous version, do so before using Odessy.')

            elif job == 'cleanup_preview':
                self.cleanup_plan = result
                self.show(
                    'Rollback backup from the most recent maintenance: '
                    f"{result['file_count']} files, {result['bytes_to_remove'] / 1024**2:.1f} MiB total"
                    '\n\nAfter cleanup the previous version cannot be restored. '
                    'Only the backups listed by this check are deleted.'
                    '\nThe application, models, configuration, workspaces and other maintenance '
                    'files are preserved.'
                    '\nTotal file size does not necessarily equal the disk space reclaimed.')
                if result['inventory']:
                    self.cleanup_button.configure(state='normal')
                self.note.set('Nothing has been deleted. Clean up backups only once you no longer '
                              'need the previous version.' if result['inventory']
                              else 'No recorded rollback backups to clean up.')

            elif job == 'cleanup':
                self.cleanup_plan = None
                self.plan = None
                self.show('Backup cleanup complete: '
                          + str(result['removed_files']) + ' files, '
                          + f"{result['removed_bytes'] / 1024**2:.1f} MiB")
                self.note.set('Application and user data preserved. '
                              'The rollback copies removed by this run cannot be restored.')

            elif job == 'preview':
                self.plan = result
                action = {'upgrade': 'A newer application version was found.',
                          'repair': 'Application files are missing or failed validation and can be repaired.',
                          'current': 'Application files match the current version; launcher entries can be refreshed.'
                          }.get(result['application_action'], result['application_action'])
                size = (sum(item['required_bytes'] for item in result.get('space_budget', [])) / 1024**2
                        if result.get('space_budget') else result['required_free_bytes'] / 1024**2)
                self.show(
                    'Application status: ' + action +
                    '\n\nAction: the application and launcher entries are prepared in a separate '
                    'directory and switched only after live model and tool validation pass.'
                    '\nComponents to repair: '
                    + (', '.join(result.get('repair_components', []))
                       or 'all base components passed validation and will be reused.') +
                    '\nPreserved: connection configuration, workspaces, healthy models and existing shortcuts.'
                    '\n\nAdditional space budget (includes cache, checked per disk): '
                    + f'{size:.1f} MiB' +
                    '\nOld components are kept as a rollback backup; a failed repair attempts to '
                    'restore the pre-maintenance components.')
                self.apply_button.configure(state='normal')
                self.note.set('Check complete — no files have been switched yet.')

            else:
                self.plan = None
                self.show('Maintenance result: ' + result['status'])
                self.note.set('Done. Close the maintenance window and open Odessy. '
                              'The rollback backup and record are preserved.')

        except queue.Empty:
            pass

        if self.close_pending and not self.busy:
            self.root.destroy()
            return
        self.callback = self.root.after(60, self.poll)

    def close(self):
        if self.busy:
            self.close_pending = True
            self.note.set('Waiting for the current operation to finish; the window will close automatically.')
        else:
            self.root.after_cancel(self.callback)
            self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--tool-bundle', type=Path)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args(argv)

    if getattr(sys, 'frozen', False):
        from installer.build.bootstrap import bundled_tools
        from installer.core.preflight import REPO_ROOT
        bundle = args.tool_bundle or bundled_tools(
            REPO_ROOT, Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')))
        executable = args.executable or Path(sys.executable)
    else:
        bundle = args.tool_bundle
        executable = args.executable

    if bundle is None or executable is None:
        raise ValueError('Source entry requires --tool-bundle and --executable.')

    import tkinter as tk
    root = tk.Tk()
    Maintenance(root, args.install_dir, bundle, executable)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
