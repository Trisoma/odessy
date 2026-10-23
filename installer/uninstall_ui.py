"""Separate graphical removal preview; deletion requires a confirmed fresh plan."""
from __future__ import annotations
import argparse
import queue
import threading
from pathlib import Path

from installer.operations import uninstall
from installer.ui import ui_theme as theme


class Uninstaller:
    def __init__(self, root, installation, *, preview=uninstall.plan,
                 remove=uninstall.execute, confirm=None):
        import tkinter as tk
        from tkinter import ttk, messagebox

        self.root = root
        self.installation = installation
        self.preview_function = preview
        self.remove_function = remove
        self.confirm = confirm or (
            lambda message: messagebox.askyesno('Confirm uninstall', message, parent=root, default='no'))
        self.plan = None
        self.busy = False
        self.close_pending = False
        self.messages = queue.Queue()
        self.thread = None

        root.title('Uninstall Odessy')
        root.geometry('820x680')
        root.minsize(740, 600)
        root.configure(background=theme.COLORS['bg'])
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        theme.configure(root)
        theme.header(root, title='Odessy', caption='Uninstall')

        body = ttk.Frame(root, padding=(32, 22, 32, 0))
        body.grid(row=2, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)

        ttk.Label(body, text='Uninstall and clean up', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(body, text='Installation: ' + str(installation), style='Hint.TLabel').grid(
            row=1, column=0, sticky='w', pady=(6, 12))

        self.keep_models = tk.BooleanVar(value=True)
        self.keep_config = tk.BooleanVar(value=True)
        choices = ttk.Frame(body)
        choices.grid(row=2, column=0, sticky='w')
        self.choices = [
            ttk.Checkbutton(choices, text='Keep model files', variable=self.keep_models),
            ttk.Checkbutton(choices, text='Keep connection configuration', variable=self.keep_config),
        ]
        for i, widget in enumerate(self.choices):
            widget.grid(row=0, column=i, padx=(0, 22))

        ttk.Label(body, text='Preview the removal scope first, then confirm. '
                             'Workspaces and the shared download cache are preserved.',
                  style='Hint.TLabel', wraplength=760).grid(row=3, column=0, sticky='w', pady=12)

        self.text = tk.Text(body, wrap='word', height=15, state='disabled',
                            background=theme.COLORS['surface'], foreground=theme.COLORS['text'],
                            relief='flat', borderwidth=0, padx=16, pady=12,
                            font=(theme.FONT, 10), highlightthickness=1,
                            highlightbackground=theme.COLORS['border'],
                            highlightcolor=theme.COLORS['border'])
        self.text.grid(row=4, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        scroll.grid(row=4, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scroll.set)

        self.note = tk.StringVar(value='Close the Odessy terminal and launcher panel, then preview.')
        ttk.Label(body, textvariable=self.note, style='Hint.TLabel', wraplength=760).grid(
            row=5, column=0, columnspan=2, sticky='w', pady=12)

        buttons = ttk.Frame(root, padding=(32, 0, 32, 20))
        buttons.grid(row=3, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)

        self.preview_button = ttk.Button(buttons, text='Preview removal scope', command=self.preview)
        self.preview_button.grid(row=0, column=1, padx=(0, 8))
        self.remove_button = ttk.Button(buttons, text='Confirm uninstall', command=self.remove,
                                        state='disabled')
        self.remove_button.grid(row=0, column=2)

        for variable in (self.keep_models, self.keep_config):
            variable.trace_add('write', self.invalidate)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.callback = root.after(60, self.poll)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def invalidate(self, *_args):
        self.plan = None
        self.remove_button.configure(state='disabled')
        self.note.set('Options changed — preview again.')

    def show(self, text):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')

    def begin(self, job, action):
        if self.busy:
            return
        self.busy = True
        for widget in [*self.choices, self.preview_button, self.remove_button]:
            widget.configure(state='disabled')
        self.note.set('Previewing…' if job == 'preview' else 'Uninstalling — please wait.')

        def work():
            try:
                self.messages.put((job, action(), None))
            except Exception as err:
                self.messages.put((job, None, str(err)))

        self.thread = threading.Thread(target=work, name='odessy-uninstall', daemon=False)
        self.thread.start()

    def options(self):
        return dict(keep_models=self.keep_models.get(), keep_config=self.keep_config.get())

    # ── Actions ──────────────────────────────────────────────────────────────

    def preview(self):
        options = self.options()
        self.plan = None
        self.begin('preview', lambda: self.preview_function(self.installation, **options))

    def remove(self):
        if self.busy or self.plan is None:
            return
        plan = self.plan
        if (plan['keep_models'], plan['keep_config']) != (self.keep_models.get(), self.keep_config.get()):
            self.invalidate()
            return
        message = ('The application, private runtime and matching shortcuts will be removed from:\n'
                   + str(self.installation) +
                   '\n\nModels: ' + ('keep' if plan['keep_models'] else 'delete') +
                   '\nConnection configuration: ' + ('keep' if plan['keep_config'] else 'delete') +
                   '\n\nWorkspaces and the shared download cache are preserved. '
                   'This cannot be undone. Continue?')
        if not self.confirm(message):
            return
        self.begin('remove', lambda: self.remove_function(
            self.installation, plan['plan_sha256'],
            keep_models=plan['keep_models'], keep_config=plan['keep_config']))

    def poll(self):
        try:
            job, result, error = self.messages.get_nowait()
            self.busy = False
            for widget in [*self.choices, self.preview_button]:
                widget.configure(state='normal')

            if error:
                self.plan = None
                self.show(error)
                self.note.set('The operation did not complete. Review the reason, then preview again. '
                              'Administrator rights will not be requested automatically.')

            elif job == 'preview':
                self.plan = result
                self.show(
                    'Component directories to remove (including files inside):\n'
                    + '\n'.join(result['components'])
                    + '\n\nEstimated size to delete: ' + f"{result['bytes_to_remove'] / 1024**2:.1f} MiB"
                    + '\nMatching shortcuts: ' + str(len(result['shortcuts']))
                    + '\nPreserved modified shortcuts: ' + str(len(result['retained_shortcuts']))
                    + '\n\nModels: ' + ('keep' if result['keep_models'] else 'delete')
                    + '\nConnection configuration: ' + ('keep' if result['keep_config'] else 'delete')
                    + '\n\nInstallation records and other top-level files not listed here are preserved. '
                      'Workspaces and the shared download cache are preserved.')
                self.remove_button.configure(state='normal')
                self.note.set('Preview complete — no files have been deleted.')

            else:
                self.plan = None
                self.remove_button.configure(state='disabled')
                self.show('Uninstall complete.\nFiles removed: ' + str(result['removed_files'])
                          + '\nShortcuts removed: ' + str(result['removed_shortcuts']))
                self.note.set('Cleaned up the confirmed scope. '
                              'Retained data and the uninstall record remain in the original directory.')

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
    args = parser.parse_args(argv)
    import tkinter as tk
    root = tk.Tk()
    Uninstaller(root, args.install_dir)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
