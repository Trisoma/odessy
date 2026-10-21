"""Chinese desktop wizard for the existing developer installation workflow."""
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

STAGES = {
    'tool_bundle': '安装资源校验', 'runtime': 'Python 与 Node 运行环境', 'dependencies': '应用依赖',
    'application': '应用文件', 'ollama_runtime': '本地模型服务', 'model': '模型文件',
    'connection': '真实模型与工具验证', 'configuration': '连接配置',
}
STATUS = {'pending': '等待', 'running': '进行中', 'completed': '已完成', 'failed': '失败', 'cancelled': '已停止'}


class Wizard:
    def __init__(self, root, options: Options, *, controller=None):
        import tkinter as tk
        from tkinter import ttk, filedialog
        self.tk = tk; self.ttk = ttk; self.filedialog = filedialog
        self.root = root; self.controller = controller or Controller()
        self.page = 0; self.active = False; self.close_pending = False
        self.preview = None; self.resume = options.resume; self.outcome = None; self.installed_root = None
        self.fields = {name: tk.StringVar(value=value) for name, value in asdict(options).items() if name != 'resume'}
        self.fields['install_dir'].trace_add('write', self._path_changed)
        self.error = tk.StringVar(); self.mode = tk.StringVar(value='新安装')
        self.current = tk.StringVar(value='准备检查安装方案')
        self.count = tk.StringVar(value='已完成 0 / 8 个阶段')
        self.root.title('Odessy 安装向导')
        self.root.geometry('1040x790'); self.root.minsize(960, 740)
        self.root.configure(background='#F4F7FB')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), foreground='#20334B')
        style.configure('TFrame', background='#F4F7FB')
        style.configure('Card.TFrame', background='#FFFFFF')
        style.configure('TLabel', background='#F4F7FB')
        style.configure('Card.TLabel', background='#FFFFFF')
        style.configure('Hint.TLabel', foreground='#62738A')
        style.configure('Error.TLabel', foreground='#B42338')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'), foreground='#15263C')
        style.configure('Accent.TButton', background='#176BCE', foreground='white', padding=(17, 10), borderwidth=0)
        style.map('Accent.TButton', background=[('disabled', '#B4C7DE'), ('active', '#125AB0')], foreground=[('disabled', '#F3F6FA')])
        style.configure('TButton', padding=(12, 7))
        style.configure('TEntry', padding=7, fieldbackground='white')
        style.configure('Treeview', rowheight=32, background='white', fieldbackground='white', borderwidth=0)
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), background='#E8EFF8', padding=8)
        style.configure('Horizontal.TProgressbar', background='#176BCE', troughcolor='#E0E9F4', borderwidth=0)
        self.root.columnconfigure(1, weight=1); self.root.rowconfigure(0, weight=1)
        nav = tk.Frame(root, background='#15263C', width=194); nav.grid(row=0, column=0, sticky='nsew'); nav.grid_propagate(False)
        tk.Label(nav, text='O', background='#176BCE', foreground='white', font=('Segoe UI', 28, 'bold'), width=3).pack(anchor='w', padx=26, pady=(35, 14))
        tk.Label(nav, text='Odessy', background='#15263C', foreground='white', font=('Segoe UI', 24, 'bold')).pack(anchor='w', padx=26)
        tk.Label(nav, text='安装向导', background='#15263C', foreground='#AABED8', font=('Microsoft YaHei UI', 11)).pack(anchor='w', padx=28, pady=(8, 42))
        self.nav_labels = []
        for number, name in enumerate(('安装设置', '确认方案', '安装进度'), 1):
            label = tk.Label(nav, text=f'{number:02d}   {name}', anchor='w', background='#15263C', foreground='#8297B2', font=('Microsoft YaHei UI', 11), pady=16)
            label.pack(fill='x', padx=25); self.nav_labels.append(label)
        tk.Label(nav, text='Windows x64\n开发验证版', justify='left', background='#15263C', foreground='#8297B2', font=('Microsoft YaHei UI', 10)).pack(side='bottom', anchor='w', padx=28, pady=28)
        main = ttk.Frame(root, padding=(28, 26)); main.grid(row=0, column=1, sticky='nsew')
        main.columnconfigure(0, weight=1); main.rowconfigure(2, weight=1)
        self.heading = ttk.Label(main, text='设置安装位置', style='Title.TLabel'); self.heading.grid(row=0, column=0, sticky='w')
        self.subtitle = ttk.Label(main, text='先检查安装方案，再下载、配置与验证。', style='Hint.TLabel', wraplength=720)
        self.subtitle.grid(row=1, column=0, sticky='w', pady=(8, 18))
        self.stack = ttk.Frame(main); self.stack.grid(row=2, column=0, sticky='nsew')
        self.stack.columnconfigure(0, weight=1); self.stack.rowconfigure(0, weight=1)
        self.pages = [ttk.Frame(self.stack) for _ in range(3)]
        for page in self.pages: page.grid(row=0, column=0, sticky='nsew')
        self.inputs = []
        self._setup_page(); self._review_page(); self._progress_page()
        footer = ttk.Frame(main); footer.grid(row=3, column=0, sticky='ew', pady=(18, 0)); footer.columnconfigure(1, weight=1)
        self.back_button = ttk.Button(footer, text='返回设置', command=lambda: self.show(0)); self.back_button.grid(row=0, column=0)
        self.close_button = ttk.Button(footer, text='关闭', command=self.close); self.close_button.grid(row=0, column=2, padx=(8, 10))
        self.primary = ttk.Button(footer, text='检查安装方案', style='Accent.TButton', command=self.primary_action)
        self.primary.grid(row=0, column=3)
        self.show(0)
        self.root.after(60, self.poll)

    def path_field(self, parent, row, text, field):
        ttk = self.ttk
        ttk.Label(parent, text=text).grid(row=row, column=0, columnspan=2, sticky='w', pady=(6, 5))
        entry = ttk.Entry(parent, textvariable=self.fields[field]); entry.grid(row=row + 1, column=0, sticky='ew')
        button = ttk.Button(parent, text='选择文件夹', command=lambda: self.browse(field)); button.grid(row=row + 1, column=1, padx=(8, 0))
        self.inputs.extend((entry, button))

    def _setup_page(self):
        ttk = self.ttk; container = self.pages[0]
        container.columnconfigure(0, weight=1); container.rowconfigure(0, weight=1)
        self.setup_canvas = self.tk.Canvas(container, background='#F4F7FB', highlightthickness=0)
        self.setup_canvas.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(container, orient='vertical', command=self.setup_canvas.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.setup_canvas.configure(yscrollcommand=scroll.set)
        page = ttk.Frame(self.setup_canvas); page.columnconfigure(0, weight=1)
        content = self.setup_canvas.create_window((0, 0), window=page, anchor='nw')
        self.setup_canvas.bind('<Configure>', lambda event: self.setup_canvas.itemconfigure(content, width=event.width))
        page.bind('<Configure>', lambda event: self.setup_canvas.configure(scrollregion=self.setup_canvas.bbox('all')))
        self.path_field(page, 0, '安装位置', 'install_dir')
        ttk.Label(page, text='请选择新建或空文件夹；也可以读取此目录已有的安装记录。', style='Hint.TLabel', wraplength=700).grid(row=2, column=0, columnspan=2, sticky='w', pady=(6, 12))
        if getattr(sys, 'frozen', False):
            ttk.Label(page, text='应用安装资源已内置，无需选择源码或工具包目录。', style='Hint.TLabel', wraplength=700).grid(row=3, column=0, columnspan=2, sticky='w', pady=(6, 10))
        else:
            self.path_field(page, 3, '安装资源目录', 'tool_bundle')
            ttk.Label(page, text='源码开发入口使用已生成的本地资源；EXE 验证版已内置。', style='Hint.TLabel', wraplength=700).grid(row=5, column=0, columnspan=2, sticky='w', pady=(6, 10))
        self.advanced = self.tk.BooleanVar(value=False)
        toggle = ttk.Checkbutton(page, text='更多设置', variable=self.advanced, command=self.toggle_advanced)
        toggle.grid(row=6, column=0, sticky='w'); self.inputs.append(toggle)
        self.extra = ttk.Frame(page); self.extra.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(5, 8)); self.extra.columnconfigure(0, weight=1)
        self.path_field(self.extra, 0, '下载缓存位置', 'cache_dir')
        options = ttk.Frame(self.extra); options.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 0)); options.columnconfigure(0, weight=1)
        ttk.Label(options, text='本机模型服务地址').grid(row=0, column=0, sticky='w')
        ttk.Label(options, text='每次调用检查超时（秒）').grid(row=0, column=1, sticky='w', padx=(12, 0))
        for column, field in enumerate(('endpoint', 'timeout')):
            entry = ttk.Entry(options, textvariable=self.fields[field], width=22 if column == 0 else 15)
            entry.grid(row=1, column=column, sticky='ew', padx=(0 if column == 0 else 12, 0), pady=(5, 0)); self.inputs.append(entry)
        self.extra.grid_remove()
        actions = ttk.Frame(page); actions.grid(row=8, column=0, columnspan=2, sticky='ew', pady=(13, 5))
        load = ttk.Button(actions, text='读取此目录的安装记录', command=self.load_record); load.pack(side='left')
        fresh = ttk.Button(actions, text='使用新安装模式', command=self.new_mode); fresh.pack(side='left', padx=8)
        self.inputs.extend((load, fresh))
        ttk.Label(page, textvariable=self.mode, style='Hint.TLabel').grid(row=9, column=0, columnspan=2, sticky='w', pady=(5, 5))
        ttk.Label(page, textvariable=self.error, style='Error.TLabel', wraplength=700).grid(row=10, column=0, columnspan=2, sticky='w')
        detail = ttk.Button(page, text='查看诊断日志', command=self.show_logs); detail.grid(row=11, column=0, sticky='w', pady=(6, 0))
        self.inputs.append(detail)
        cleanup=ttk.Button(page,text='卸载/清理此目录…',command=self.open_uninstaller)
        cleanup.grid(row=12,column=0,sticky='w',pady=(12,0));self.inputs.append(cleanup)
        repair=ttk.Button(page,text='修复/升级此目录…',command=self.open_maintenance)
        repair.grid(row=13,column=0,sticky='w',pady=(12,0));self.inputs.append(repair)

    def _review_page(self):
        ttk = self.ttk; page = self.pages[1]; page.columnconfigure(0, weight=1)
        self.review_text = self.tk.StringVar()
        ttk.Label(page, textvariable=self.review_text, wraplength=710, justify='left').grid(row=0, column=0, sticky='nw', pady=(4, 16))
        self.space = self.tk.StringVar()
        ttk.Label(page, textvariable=self.space, wraplength=710, justify='left').grid(row=1, column=0, sticky='w', pady=10)
        ttk.Separator(page).grid(row=2, column=0, sticky='ew', pady=18)
        ttk.Label(page, text='将准备运行环境、应用和本地模型，并执行两次真实调用检查。\n已有校验缓存会复用；不会覆盖其他安装目录。', wraplength=700).grid(row=3, column=0, sticky='w', pady=8)
        ttk.Label(page, text='当前开发版完成安装验证后，会停止本轮新启动的模型服务。\n安装完成后可打开启动面板；公开下载与全新电脑验收将在后续完成。', style='Hint.TLabel', wraplength=700).grid(row=4, column=0, sticky='w', pady=14)
        self.review_notice = self.tk.StringVar()
        ttk.Label(page, textvariable=self.review_notice, style='Hint.TLabel', wraplength=700).grid(row=5, column=0, sticky='w', pady=10)

    def _progress_page(self):
        ttk = self.ttk; page = self.pages[2]; page.columnconfigure(0, weight=1); page.rowconfigure(3, weight=1)
        ttk.Label(page, textvariable=self.current, wraplength=710).grid(row=0, column=0, sticky='w', pady=(4, 10))
        self.bar = ttk.Progressbar(page, maximum=8, mode='determinate'); self.bar.grid(row=1, column=0, sticky='ew')
        ttk.Label(page, textvariable=self.count, style='Hint.TLabel').grid(row=2, column=0, sticky='w', pady=(7, 12))
        self.tabs = ttk.Notebook(page); self.tabs.grid(row=3, column=0, sticky='nsew')
        stage_page = ttk.Frame(self.tabs); log_page = ttk.Frame(self.tabs)
        self.tabs.add(stage_page, text='安装阶段'); self.tabs.add(log_page, text='诊断日志')
        self.tree = ttk.Treeview(stage_page, columns=('name', 'status'), show='headings', height=8, selectmode='none')
        self.tree.heading('name', text='阶段'); self.tree.heading('status', text='状态')
        self.tree.column('name', width=420, stretch=True); self.tree.column('status', width=140, stretch=False)
        self.tree.pack(fill='both', expand=True)
        for name, title in STAGES.items(): self.tree.insert('', 'end', iid=name, values=(title, '等待'))
        self.tree.tag_configure('completed', foreground='#157347'); self.tree.tag_configure('failed', foreground='#B42338')
        self.tree.tag_configure('running', foreground='#176BCE')
        self.log = self.tk.Text(log_page, wrap='word', background='#FFFFFF', foreground='#334A64', font=('Consolas', 10), relief='flat', state='disabled')
        scroll = ttk.Scrollbar(log_page, command=self.log.yview); self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y'); self.log.pack(fill='both', expand=True)
        self.result_note = self.tk.StringVar()
        ttk.Label(page, textvariable=self.result_note, style='Hint.TLabel', wraplength=710).grid(row=4, column=0, sticky='w', pady=(12, 0))

        shortcuts = ttk.Frame(page); shortcuts.grid(row=5,column=0,sticky='w',pady=(10,0))
        self.shortcut_desktop=self.tk.BooleanVar(value=True); self.shortcut_menu=self.tk.BooleanVar(value=True)
        self.shortcut_choices=[ttk.Checkbutton(shortcuts,text='桌面',variable=self.shortcut_desktop),
                               ttk.Checkbutton(shortcuts,text='开始菜单',variable=self.shortcut_menu)]
        for i,widget in enumerate(self.shortcut_choices): widget.grid(row=0,column=i,padx=(0,12))
        self.shortcut_button=ttk.Button(shortcuts,text='创建/补齐快捷方式',command=self.create_shortcuts)
        self.shortcut_button.grid(row=0,column=2)

    def open_maintenance(self):
        if self.active:return
        try:
            root=Path(self.fields['install_dir'].get().strip())
            if not root.is_absolute():raise ValueError('请先选择已有安装的绝对路径。')
            arguments=['--install-dir',str(root)]
            if not getattr(sys,'frozen',False):
                self.error.set('请使用新版独立 EXE 打开修复升级窗口。');return
            subprocess.Popen(entry_command('installer.maintenance_ui',arguments),cwd=Path(sys.executable).parent,
                env=independent_environment(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,ValueError) as error:self.error.set(str(error)[:300])

    def open_uninstaller(self):
        if self.active: return
        try:
            root=Path(self.fields['install_dir'].get().strip())
            if not root.is_absolute(): raise ValueError('请先选择已有安装的绝对路径。')
            subprocess.Popen(entry_command('installer.uninstall_ui',['--install-dir',str(root)]),
                cwd=Path(sys.executable).parent if getattr(sys,'frozen',False) else REPO_ROOT,
                env=independent_environment(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,ValueError) as error:self.error.set(str(error)[:300])

    def create_shortcuts(self):
        if self.active or self.outcome != 'completed' or self.installed_root is None: return
        if not getattr(sys,'frozen',False):
            self.result_note.set('请使用独立 EXE 创建启动入口和快捷方式。'); return
        if not self.shortcut_desktop.get() and not self.shortcut_menu.get():
            self.result_note.set('请至少选择桌面或开始菜单中的一个位置。'); return
        self.result_note.set('正在创建启动入口和快捷方式…')
        source=self.installed_root/'launcher/OdessyLauncher.exe'
        if not source.is_file():source=Path(sys.executable)
        self.begin('shortcuts',dict(install_dir=str(self.installed_root),executable=str(source),
                                  desktop=self.shortcut_desktop.get(),start_menu=self.shortcut_menu.get()))

    def options(self):
        return Options(**{name: variable.get() for name, variable in self.fields.items()}, resume=self.resume)

    def _path_changed(self, *args):
        self.resume = False
        if hasattr(self, 'mode'): self.mode.set('新安装')
        self.preview = None

    def fill_options(self, options):
        for name, value in options.items():
            if name != 'resume': self.fields[name].set(value)
        self.resume = options['resume']; self.mode.set('恢复已保存的安装' if self.resume else '新安装')
        self.preview = None

    def browse(self, field):
        path = self.filedialog.askdirectory(parent=self.root, title='选择文件夹', mustexist=False)
        if path: self.fields[field].set(path)

    def toggle_advanced(self):
        self.extra.grid() if self.advanced.get() else self.extra.grid_remove()

    def new_mode(self):
        self.resume = False; self.mode.set('新安装'); self.preview = None; self.error.set('')

    def show(self, page):
        self.page = page; self.pages[page].tkraise()
        titles = ('设置安装位置', '确认安装方案', '安装进度与结果')
        subtitles = ('先检查安装方案，再下载、配置与验证。', '检查目录与磁盘空间，然后开始安装。', '进度按阶段显示，具体耗时取决于下载与模型检查。')
        self.heading.configure(text=titles[page]); self.subtitle.configure(text=subtitles[page])
        for index, label in enumerate(self.nav_labels): label.configure(foreground='white' if index == page else '#8297B2')
        self.sync_controls()

    def begin(self, job, payload):
        if self.active: return
        self.error.set(''); self.active = True
        try:
            self.controller.start(job, payload)
        except Exception as error:
            self.active = False; self.error.set(str(error))
        self.sync_controls()

    def primary_action(self):
        if self.active: return
        if self.page == 0:
            self.begin('preview', self.options())
        elif self.page == 1:
            if self.preview is None or fingerprint(asdict(self.options())) != self.preview['options_sha256']:
                self.error.set('设置已变化，请重新检查方案。'); self.show(0); return
            if not self.preview['sufficient']: return
            self.outcome = None; self.current.set('正在开始安装…'); self.result_note.set('')
            self.bar['value'] = 0; self.count.set('已完成 0 / 8 个阶段')
            for name, title in STAGES.items(): self.tree.item(name, values=(title, '等待'), tags=())
            self.show(2); self.tabs.select(0); self.begin('execute', self.preview)
        elif self.outcome == 'completed':
            if self.installed_root is not None:
                try:
                    subprocess.Popen(entry_command('installer.launcher', ['--install-dir', str(self.installed_root)]),
                                     cwd=Path(sys.executable).parent if getattr(sys, 'frozen', False) else REPO_ROOT,
                                     env=independent_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except OSError as error:
                    self.result_note.set(str(error)); self.append_log(str(error))
        elif self.preview is not None:
            self.fill_options({**self.preview['options'], 'resume': state_path(Path(self.preview['plan']['install_dir'])).is_file()})
            self.show(0); self.begin('preview', self.options())
        else:
            self.show(0)

    def load_record(self):
        self.begin('record', self.options())

    def show_logs(self):
        self.show(2); self.tabs.select(1)

    def sync_controls(self):
        for widget in getattr(self, 'inputs', []): widget.configure(state='disabled' if self.active else 'normal')
        shortcut_state='normal' if not self.active and self.outcome=='completed' else 'disabled'
        for widget in [*getattr(self,'shortcut_choices',[]),*([self.shortcut_button] if hasattr(self,'shortcut_button') else [])]:
            widget.configure(state=shortcut_state)
        if not hasattr(self, 'primary'): return
        self.back_button.configure(state='disabled' if self.active or self.page == 0 else 'normal')
        if self.active:
            self.primary.configure(text='处理中…', state='disabled')
            self.close_button.configure(command=self.stop, text='停止安装' if self.controller.job == 'execute' else '等待检查', state='normal' if self.controller.job == 'execute' and not self.controller.cancel.is_set() else 'disabled')
        else:
            text = '检查安装方案' if self.page == 0 else ('校验并恢复' if self.resume else '开始安装') if self.page == 1 else '启动 Odessy' if self.outcome == 'completed' else '检查并重试' if self.preview else '返回设置'
            self.primary.configure(text=text, state='disabled' if self.page == 1 and (not self.preview or not self.preview['sufficient']) else 'normal')
            self.close_button.configure(command=self.close, text='关闭', state='normal')

    def append_log(self, value):
        self.log.configure(state='normal'); self.log.insert('end', value + '\n')
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > 1200: self.log.delete('1.0', f'{lines - 1200}.0')
        self.log.see('end'); self.log.configure(state='disabled')

    def progress(self, event):
        name = event['step']
        if name in STAGES:
            self.tree.item(name, values=(STAGES[name], STATUS.get(event['status'], event['status'])), tags=(event['status'],))
        completed = max(0, min(8, event['completed_steps']))
        self.bar['value'] = completed; self.count.set(f'已完成 {completed} / 8 个阶段')
        if self.controller.cancel.is_set():
            self.current.set('正在安全停止，请等待当前步骤结束…')
        else:
            self.current.set(f'{STAGES.get(name, name)}：{STATUS.get(event["status"], event["status"])}')
        self.append_log(f'[{event["index"]}/{event["total"]}] {name}: {event["status"]}' + (' — ' + event['error'] if event.get('error') else ''))

    def handle(self, message):
        kind = message['kind']
        if kind == 'log': self.append_log(message['text']); return
        if kind == 'progress': self.progress(message['event']); return
        self.active = False
        if message.get('job')=='shortcuts':
            if kind=='finished':
                self.result_note.set('快捷方式已准备。以后可从桌面或开始菜单打开 Odessy。')
                self.append_log('Persistent launcher: '+message['result']['executable'])
            else:
                self.result_note.set('快捷方式未完成，应用安装保留：'+message.get('message','请查看诊断日志。'))
                self.append_log(message.get('error','快捷方式未完成。'))
            self.sync_controls(); return
        if kind == 'error':
            self.error.set(message['message'][:320]); self.append_log(message.get('error', message['message']))
            if message['job'] == 'execute':
                self.outcome = 'failed'; self.current.set('安装未完成'); self.result_note.set(message['message'][:400]); self.show(2)
            else: self.show(0)
        elif kind == 'cancelled':
            self.outcome = 'cancelled'; self.current.set('已安全停止'); self.result_note.set('已有完整组件和检查点已保留；可检查方案后恢复。')
            if message['job'] == 'execute': self.show(2)
        elif kind == 'finished':
            result = message['result']
            if message['job'] == 'preview':
                self.preview = result
                plan = result['plan']
                self.review_text.set(f'安装方式：{"恢复并重新校验" if result["resume"] else "新安装"}\n\n安装位置：{plan["install_dir"]}\n\n缓存位置：{plan["cache_dir"]}\n\n本地模型：{plan["model"]}')
                lines = []
                for volume in result['volumes']:
                    kinds = ' / '.join('缓存' if key == 'cache' else '安装' for key in volume['contributions'] if key != 'models')
                    lines.append(f'{kinds}磁盘：需要 {volume["required_bytes"]/1024**3:.2f} GiB，可用 {volume["free_bytes"]/1024**3:.2f} GiB')
                self.space.set('\n'.join(lines) + '\n\n预算包含缓存和临时文件，实际下载量取决于缓存命中。')
                self.review_notice.set('空间检查通过，可以开始。' if result['sufficient'] else '空间不足，请返回调整安装位置或缓存位置。')
                self.show(1)
            elif message['job'] == 'record':
                self.fill_options(result['options']); state = result['state']; self.outcome = None
                if state['status'] == 'completed':
                    self.outcome = 'completed'; self.installed_root = Path(result['options']['install_dir'])
                self.show(2); self.current.set('已读取安装记录：' + STATUS.get(state['status'], state['status']))
                completed = sum(step['status'] == 'completed' for step in state['steps'])
                self.bar['value'] = completed; self.count.set(f'已完成 {completed} / 8 个阶段')
                for step in state['steps']:
                    if step['name'] in STAGES: self.tree.item(step['name'], values=(STAGES[step['name']], STATUS.get(step['status'], step['status'])), tags=(step['status'],))
                    if step.get('error'): self.append_log(step['name'] + ': ' + step['error'])
                self.result_note.set('记录仅供查看。返回设置后可检查恢复方案；跨安装器版本不会强制恢复。')
            else:
                if result.get('status') != 'completed' or not result.get('summary', {}).get('workflow_complete'):
                    self.outcome = 'failed'; self.current.set('流程未确认完成，请查看诊断日志。')
                else:
                    self.outcome = 'completed'; self.installed_root = Path(self.preview['plan']['install_dir']); self.bar['value'] = 8; self.count.set('已完成 8 / 8 个阶段')
                    self.current.set('安装与真实调用验证已完成')
                    self.result_note.set('连接配置已保存。本轮新启动的模型服务已停止，已有服务保持原状。\n可点击“启动 Odessy”选择工作文件夹。公开下载与全新电脑验收尚未完成。')
                self.show(2)
        self.sync_controls()

    def poll(self):
        for _ in range(180):
            try: message = self.controller.messages.get_nowait()
            except queue.Empty: break
            self.handle(message)
        if self.close_pending and not self.active:
            self.root.destroy(); return
        self.sync_controls(); self.root.after(60, self.poll)

    def stop(self):
        if self.active:
            self.controller.request_cancel()
            self.current.set('正在安全停止，请等待当前步骤结束…')
            self.result_note.set('完整组件与检查点会保留。当前调用结束后，可以检查方案并恢复。')
            self.sync_controls()

    def close(self):
        if self.active:
            self.close_pending = True; self.controller.request_cancel()
            self.current.set('正在安全停止，请保持窗口打开，等待当前步骤结束…')
            self.result_note.set('不会强制结束其他服务。当前模型检查可能需要等待超时或正常结束。')
            self.show(2); self.sync_controls()
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
        except tk.TclError as error:
            print('无法打开安装界面：', error)
            return 1
    except (ImportError, RuntimeError) as error:
        print('当前开发启动环境需要 Tkinter/Tcl。请使用 EXE 验证版或带 Tkinter 的 Python：', error)
        return 1
    Wizard(root, Options(str(args.install_dir), str(args.cache_dir), str(args.tool_bundle or '')))
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
