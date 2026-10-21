"""Development graphical entry for launching an already completed installation."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path
from installer.checkpoint import read_state
from installer.preflight import REPO_ROOT
from installer.start import workspace_path
from installer.entrypoints import entry_command, independent_environment


def launch_console(root: Path, workspace: Path, *, popen=subprocess.Popen):
    if os.name != 'nt': raise ValueError('This launcher currently targets Windows.')
    workspace = workspace_path(root, workspace)
    from installer.maintenance import require_idle
    require_idle(root)
    from installer.uninstall import removal_started
    if removal_started(root): raise ValueError('此安装已执行卸载，不能启动。')
    state = read_state(root)
    if state['status'] != 'completed': raise ValueError('请先完成安装，再启动 Odessy。')
    if Path(state['plan']['install_dir']).resolve() != root.resolve(): raise ValueError('安装目录已移动，无法启动。')
    # Argument arrays preserve spaces, Unicode and shell metacharacters; no shell interpolation.
    return popen(entry_command('installer.start', ['--install-dir', str(root), '--workspace', str(workspace),
                  '--pause-on-exit']), cwd=Path(sys.executable).parent if getattr(sys, 'frozen', False) else REPO_ROOT,
                 env=independent_environment(), creationflags=subprocess.CREATE_NEW_CONSOLE)


class Launcher:
    def __init__(self, root, installation: Path, *, launch=launch_console):
        import tkinter as tk
        from tkinter import ttk, filedialog
        self.root = root; self.installation = installation; self.launch = launch; self.process = None
        root.title('启动 Odessy'); root.geometry('750x560'); root.minsize(700, 560)
        root.configure(background='#F4F7FB')
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI',10), foreground='#20334B')
        style.configure('TFrame', background='#F4F7FB'); style.configure('TLabel', background='#F4F7FB')
        style.configure('TButton', padding=(12,8)); style.configure('TEntry', padding=7)
        frame=ttk.Frame(root,padding=28); frame.pack(fill='both',expand=True); frame.columnconfigure(0,weight=1)
        ttk.Label(frame,text='开始使用 Odessy',font=('Microsoft YaHei UI',20,'bold')).grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Label(frame,text='选择允许 Odessy 操作的工作文件夹。',wraplength=640).grid(row=1,column=0,columnspan=2,sticky='w',pady=(12,18))
        ttk.Label(frame,text='安装位置：'+str(installation),wraplength=640).grid(row=2,column=0,columnspan=2,sticky='w',pady=(0,18))
        ttk.Label(frame,text='工作文件夹').grid(row=3,column=0,sticky='w')
        self.workspace=tk.StringVar(); self.message=tk.StringVar(value='启动时自动检查安装并准备本地模型服务。')
        self.entry=ttk.Entry(frame,textvariable=self.workspace); self.entry.grid(row=4,column=0,sticky='ew',pady=8)
        def browse():
            folder=filedialog.askdirectory(parent=root,title='选择工作文件夹',mustexist=True)
            if folder: self.workspace.set(folder)
        self.browse=ttk.Button(frame,text='选择文件夹',command=browse); self.browse.grid(row=4,column=1,padx=(10,0))
        ttk.Label(frame,text='将在独立终端中输入请求，并保留原有操作确认。\n输入 /exit 或按 Ctrl+C 退出；仅清理本次启动的服务。\n关闭此面板不会关闭已打开的终端。',wraplength=640).grid(row=5,column=0,columnspan=2,sticky='w',pady=12)
        ttk.Label(frame,textvariable=self.message,wraplength=640).grid(row=6,column=0,columnspan=2,sticky='w',pady=8)
        self.button=ttk.Button(frame,text='打开 Odessy 终端',command=self.start); self.button.grid(row=7,column=0,columnspan=2,sticky='e',pady=8)
        self.callback = root.after(250,self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def start(self):
        if self.process is not None and self.process.poll() is None: return
        try:
            if not self.workspace.get().strip(): raise ValueError('请先选择工作文件夹。')
            self.process=self.launch(self.installation,Path(self.workspace.get()))
            self.message.set('终端已打开。请在终端中输入请求；退出后模型服务会自动清理。')
        except (OSError,ValueError,KeyError,TypeError) as error: self.message.set(str(error)[:250])
        self.poll(schedule=False)

    def poll(self,*,schedule=True):
        active=self.process is not None and self.process.poll() is None
        self.button.configure(state='disabled' if active else 'normal')
        if self.process is not None and not active:
            self.message.set('终端已关闭，可重新选择工作文件夹并启动。'); self.process=None
        if schedule: self.callback = self.root.after(250,self.poll)


    def close(self):
        # The separately opened console owns its session, independent of this panel.
        self.root.after_cancel(self.callback)
        self.root.destroy()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--install-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        import tkinter as tk
        root=tk.Tk()
    except ImportError as error:
        print('Tkinter is required by this development launcher:',error); return 1
    except tk.TclError as error:
        print('Cannot open graphical launcher:',error); return 1
    Launcher(root,args.install_dir); root.mainloop(); return 0


if __name__ == '__main__': raise SystemExit(main())
