"""Separate graphical removal preview; deletion requires a confirmed fresh plan."""
from __future__ import annotations
import argparse
import queue
import threading
from pathlib import Path
from installer import uninstall


class Uninstaller:
    def __init__(self,root,installation,*,preview=uninstall.plan,remove=uninstall.execute,confirm=None):
        import tkinter as tk
        from tkinter import ttk,messagebox
        self.root=root;self.installation=installation;self.preview_function=preview;self.remove_function=remove
        self.confirm=confirm or (lambda message:messagebox.askyesno('确认卸载',message,parent=root,default='no'))
        self.plan=None;self.busy=False;self.close_pending=False;self.messages=queue.Queue();self.thread=None
        root.title('卸载 Odessy');root.geometry('800x660');root.minsize(700,590)
        frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True);frame.columnconfigure(0,weight=1);frame.rowconfigure(4,weight=1)
        ttk.Label(frame,text='卸载与清理',font=('Microsoft YaHei UI',20,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(frame,text='安装位置：'+str(installation),wraplength=720).grid(row=1,column=0,sticky='w',pady=12)
        self.keep_models=tk.BooleanVar(value=True);self.keep_config=tk.BooleanVar(value=True)
        choices=ttk.Frame(frame);choices.grid(row=2,column=0,sticky='w')
        self.choices=[ttk.Checkbutton(choices,text='保留模型文件',variable=self.keep_models),ttk.Checkbutton(choices,text='保留连接配置',variable=self.keep_config)]
        for i,widget in enumerate(self.choices):widget.grid(row=0,column=i,padx=(0,20))
        ttk.Label(frame,text='先预览删除范围，再确认卸载。工作文件夹与共享下载缓存会保留。',wraplength=720).grid(row=3,column=0,sticky='w',pady=12)
        self.text=tk.Text(frame,wrap='word',height=15,state='disabled');self.text.grid(row=4,column=0,sticky='nsew')
        self.note=tk.StringVar(value='请关闭 Odessy 终端与启动面板，然后预览。')
        ttk.Label(frame,textvariable=self.note,wraplength=720).grid(row=5,column=0,sticky='w',pady=12)
        buttons=ttk.Frame(frame);buttons.grid(row=6,column=0,sticky='e')
        self.preview_button=ttk.Button(buttons,text='预览清理范围',command=self.preview);self.preview_button.pack(side='left',padx=6)
        self.remove_button=ttk.Button(buttons,text='确认并卸载',command=self.remove,state='disabled');self.remove_button.pack(side='left',padx=6)
        for variable in (self.keep_models,self.keep_config):variable.trace_add('write',self.invalidate)
        root.protocol('WM_DELETE_WINDOW',self.close);self.callback=root.after(60,self.poll)

    def invalidate(self,*args):
        self.plan=None;self.remove_button.configure(state='disabled')
        self.note.set('选项已变化，请重新预览。')

    def show(self,text):
        self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',text);self.text.configure(state='disabled')

    def begin(self,job,action):
        if self.busy:return
        self.busy=True
        for widget in [*self.choices,self.preview_button,self.remove_button]:widget.configure(state='disabled')
        self.note.set('正在预览…' if job=='preview' else '正在卸载，请等待完成。')
        def work():
            try:self.messages.put((job,action(),None))
            except Exception as error:self.messages.put((job,None,str(error)))
        self.thread=threading.Thread(target=work,name='odessy-uninstall',daemon=False);self.thread.start()

    def options(self):return dict(keep_models=self.keep_models.get(),keep_config=self.keep_config.get())

    def preview(self):
        options=self.options();self.plan=None
        self.begin('preview',lambda:self.preview_function(self.installation,**options))

    def remove(self):
        if self.busy or self.plan is None:return
        plan=self.plan
        if (plan['keep_models'],plan['keep_config'])!=(self.keep_models.get(),self.keep_config.get()):self.invalidate();return
        message=('将卸载以下位置的应用、私有运行环境和匹配的快捷方式：\n'+str(self.installation)+
                 '\n\n模型：'+('保留' if plan['keep_models'] else '删除')+'\n连接配置：'+('保留' if plan['keep_config'] else '删除')+
                 '\n\n工作文件夹和共享下载缓存保留。删除后无法撤销，是否继续？')
        if not self.confirm(message):return
        self.begin('remove',lambda:self.remove_function(self.installation,plan['plan_sha256'],keep_models=plan['keep_models'],keep_config=plan['keep_config']))

    def poll(self):
        try:
            job,result,error=self.messages.get_nowait();self.busy=False
            for widget in [*self.choices,self.preview_button]:widget.configure(state='normal')
            if error:
                self.plan=None;self.show(error);self.note.set('操作未完成。请查看原因，处理后重新预览；不会自动申请管理员权限。')
            elif job=='preview':
                self.plan=result
                self.show('将清理的组件目录（包括目录内文件）：\n'+ '\n'.join(result['components'])+
                    '\n\n预计删除文件大小：'+f"{result['bytes_to_remove']/1024**2:.1f} MiB"+
                    '\n匹配的快捷方式：'+str(len(result['shortcuts']))+
                    '\n保留的已修改快捷方式：'+str(len(result['retained_shortcuts']))+
                    '\n\n模型：'+('保留' if result['keep_models'] else '删除')+
                    '\n连接配置：'+('保留' if result['keep_config'] else '删除')+
                    '\n\n安装记录及目录内其他未列出的顶层文件会保留。工作文件夹与共享下载缓存保留。')
                self.remove_button.configure(state='normal');self.note.set('预览完成，尚未删除任何文件。')
            else:
                self.plan=None;self.remove_button.configure(state='disabled')
                self.show('卸载完成。\n删除文件数：'+str(result['removed_files'])+'\n删除快捷方式数：'+str(result['removed_shortcuts']))
                self.note.set('已按确认范围清理。保留数据和卸载记录仍在原目录。')
        except queue.Empty:pass
        if self.close_pending and not self.busy:self.root.destroy();return
        self.callback=self.root.after(60,self.poll)

    def close(self):
        if self.busy:
            self.close_pending=True;self.note.set('正在等待当前操作结束，完成后自动关闭。')
        else:self.root.after_cancel(self.callback);self.root.destroy()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    import tkinter as tk
    root=tk.Tk();Uninstaller(root,args.install_dir);root.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
