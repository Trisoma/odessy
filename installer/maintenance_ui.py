"""Graphical staged application repair/update and explicit rollback."""
import argparse
import os
import queue
import sys
import threading
from pathlib import Path
from installer import maintenance,component_repair,backup_cleanup


class Maintenance:
    def __init__(self,root,installation,bundle,executable,*,preview=component_repair.plan,apply=component_repair.execute,recover=maintenance.recover,cleanup_preview=backup_cleanup.plan,cleanup=backup_cleanup.execute,confirm=None):
        import tkinter as tk
        from tkinter import ttk,messagebox
        self.root=root;self.installation=installation;self.bundle=bundle;self.executable=executable
        self.preview_function=preview;self.apply_function=apply;self.recover_function=recover
        self.confirm=confirm or (lambda text:messagebox.askyesno('确认维护',text,parent=root,default='no'))
        self.cleanup_preview_function=cleanup_preview;self.cleanup_function=cleanup;self.cleanup_plan=None
        self.plan=None;self.busy=False;self.close_pending=False;self.thread=None;self.messages=queue.Queue()
        root.title('修复与升级 Odessy');root.geometry('820x650');root.minsize(720,590)
        frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True);frame.columnconfigure(0,weight=1);frame.rowconfigure(3,weight=1)
        ttk.Label(frame,text='修复与升级',font=('Microsoft YaHei UI',20,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(frame,text='安装位置：'+str(installation),wraplength=730).grid(row=1,column=0,sticky='w',pady=12)
        ttk.Label(frame,text='检测并修复私有组件、应用和启动入口；健康模型复用，配置与工作文件夹保留。请先关闭该安装的所有窗口。',wraplength=730).grid(row=2,column=0,sticky='w',pady=8)
        self.text=tk.Text(frame,height=17,wrap='word',state='disabled');self.text.grid(row=3,column=0,sticky='nsew')
        self.note=tk.StringVar(value='先检查基础组件和新版内容，再确认执行。')
        ttk.Label(frame,textvariable=self.note,wraplength=730).grid(row=4,column=0,sticky='w',pady=12)
        buttons=ttk.Frame(frame);buttons.grid(row=5,column=0,sticky='e')
        self.preview_button=ttk.Button(buttons,text='检查维护方案',command=self.preview);self.preview_button.pack(side='left',padx=5)
        self.apply_button=ttk.Button(buttons,text='确认并修复升级',command=self.apply,state='disabled');self.apply_button.pack(side='left',padx=5)
        self.rollback_button=ttk.Button(buttons,text='恢复原版本',command=self.rollback);self.rollback_button.pack(side='left',padx=5)
        self.cleanup_preview_button=ttk.Button(buttons,text='查看备份占用',command=self.preview_cleanup);self.cleanup_preview_button.pack(side='left',padx=5)
        self.cleanup_button=ttk.Button(buttons,text='清理备份',command=self.cleanup,state='disabled');self.cleanup_button.pack(side='left',padx=5)
        self.callback=root.after(60,self.poll);root.protocol('WM_DELETE_WINDOW',self.close)

    def show(self,text):
        self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',text);self.text.configure(state='disabled')

    def begin(self,job,action):
        if self.busy:return
        self.busy=True
        for button in (self.preview_button,self.apply_button,self.rollback_button,self.cleanup_preview_button,self.cleanup_button):button.configure(state='disabled')
        self.note.set('正在检查，请等待…' if job=='preview' else '正在维护与验证，请等待完成。')
        def work():
            try:self.messages.put((job,action(),None))
            except Exception as error:self.messages.put((job,None,str(error)))
        self.thread=threading.Thread(target=work,name='odessy-maintenance',daemon=False);self.thread.start()

    def preview(self):
        self.cleanup_plan=None;self.plan=None;self.begin('preview',lambda:self.preview_function(self.installation,self.bundle,self.executable))

    def apply(self):
        if self.busy or self.plan is None:return
        plan=self.plan
        if not self.confirm('将准备并验证新版应用和启动入口，再切换到此安装：\n'+str(self.installation)+'\n\n待修复组件：'+('、'.join(plan.get('repair_components',[])) or '无，复用基础组件')+'\n配置与工作文件夹保留；旧组件会保留为回退备份。是否继续？'):return
        self.begin('apply',lambda:self.apply_function(self.installation,self.bundle,self.executable,plan['plan_sha256']))

    def rollback(self):
        if self.busy:return
        if not self.confirm('将尝试恢复最近一次维护前的应用与启动入口。修复或更新的文件会移到保留目录，恢复本次维护前的组件；配置保持原位。是否继续？'):return
        self.begin('rollback',lambda:self.recover_function(self.installation))

    def preview_cleanup(self):
        if self.busy:return
        self.plan=None;self.cleanup_plan=None
        self.begin('cleanup_preview',lambda:self.cleanup_preview_function(self.installation))

    def cleanup(self):
        if self.busy or self.cleanup_plan is None:return
        plan=self.cleanup_plan
        if not self.confirm('将永久删除此安装最近一次维护的回退备份：\n'+str(self.installation)+
            '\n文件大小合计：'+f"{plan['bytes_to_remove']/1024**2:.1f} MiB"+
            '\n清理后无法恢复本次维护前的版本。应用、模型、配置和工作文件夹保留。是否继续？'):return
        self.begin('cleanup',lambda:self.cleanup_function(self.installation,plan['plan_sha256']))

    def poll(self):
        try:
            job,result,error=self.messages.get_nowait();self.busy=False
            self.preview_button.configure(state='normal');self.rollback_button.configure(state='normal');self.cleanup_preview_button.configure(state='normal')
            if error:
                self.plan=None;self.cleanup_plan=None;self.show(error)
                self.note.set('备份未清理完。请重新查看范围；若已开始删除，本次回退将不可用。' if job=='cleanup' else '维护未完成。若提示恢复原版本，请先执行恢复再使用。')
            elif job=='cleanup_preview':
                self.cleanup_plan=result
                self.show('最近一次维护的回退备份：'+f"{result['file_count']} 个文件，文件大小合计 {result['bytes_to_remove']/1024**2:.1f} MiB"+
                    '\n\n清理后将无法恢复本次维护前的版本。只删除本次检查列出的备份。'+
                    '\n应用、模型、配置、工作文件夹和其他维护文件继续保留。'+
                    '\n文件大小合计不保证等同于实际释放的磁盘空间。')
                if result['inventory']:self.cleanup_button.configure(state='normal')
                self.note.set('尚未删除。确认不再需要恢复原版本后，才清理备份。' if result['inventory'] else '没有可清理的已记录回退备份。')
            elif job=='cleanup':
                self.cleanup_plan=None;self.plan=None
                self.show('备份清理完成：'+str(result['removed_files'])+' 个文件，'+f"{result['removed_bytes']/1024**2:.1f} MiB")
                self.note.set('应用与用户数据保留。本次已清理的回退副本无法恢复。')
            elif job=='preview':
                self.plan=result;self.show('应用状态：'+{'upgrade':'发现新版应用','repair':'应用文件缺失或校验失败，可修复','current':'应用文件与当前版本一致，可刷新启动入口'}.get(result['application_action'],result['application_action'])+
                    '\n\n操作：在独立目录准备应用和启动入口，真实模型与工具验证通过后切换。'+
                    '\n待修复组件：'+('、'.join(result.get('repair_components',[])) or '基础组件校验通过，全部复用。')+
                    '\n保留：连接配置、工作文件夹、健康模型和现有快捷方式。'+
                    '\n\n额外空间预算（含缓存，各磁盘分别检查）：'+f"{sum(item['required_bytes'] for item in result.get('space_budget',[]))/1024**2 if result.get('space_budget') else result['required_free_bytes']/1024**2:.1f} MiB"+
                    '\n旧组件会保留作回退备份；修复失败会尝试恢复维护前的组件。')
                self.apply_button.configure(state='normal');self.note.set('检查完成，尚未切换任何文件。')
            else:
                self.plan=None;self.show('维护结果：'+result['status'])
                self.note.set('完成。可关闭维护窗口，再打开 Odessy；回退备份与记录保留。')
        except queue.Empty:pass
        if self.close_pending and not self.busy:self.root.destroy();return
        self.callback=self.root.after(60,self.poll)

    def close(self):
        if self.busy:self.close_pending=True;self.note.set('等待当前操作结束，随后自动关闭。')
        else:self.root.after_cancel(self.callback);self.root.destroy()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install-dir',type=Path,required=True)
    parser.add_argument('--tool-bundle',type=Path);parser.add_argument('--executable',type=Path)
    args=parser.parse_args(argv)
    if getattr(sys,'frozen',False):
        from installer.bootstrap import bundled_tools
        from installer.preflight import REPO_ROOT
        bundle=args.tool_bundle or bundled_tools(REPO_ROOT,Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local')))
        executable=args.executable or Path(sys.executable)
    else:bundle=args.tool_bundle;executable=args.executable
    if bundle is None or executable is None:raise ValueError('源码入口需提供 --tool-bundle 与 --executable。')
    import tkinter as tk
    root=tk.Tk();Maintenance(root,args.install_dir,bundle,executable);root.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
