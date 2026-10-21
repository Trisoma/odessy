"""Explicitly discard unchanged rollback copies from the latest maintenance."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from installer import maintenance
from installer.checkpoint import fingerprint,read_state,writer_lock
from installer.model import guard_path
from installer import pathing
from installer.uninstall import _tree,_entry,removal_started,running_processes


def plan(root,*,probe=running_processes):
    guard_path(root);root=root.resolve()
    if (root/'.git').exists() or (root/'pyproject.toml').exists():raise ValueError('源码目录不能清理维护备份。')
    if root in Path(sys.executable).resolve().parents:raise ValueError('请从安装目录外的安装器执行备份清理。')
    if removal_started(root):raise ValueError('正在卸载的安装不能清理维护备份。')
    maintenance.require_idle(root)
    state=read_state(root)
    if state['status']!='completed' or not pathing.same(state['plan'].get('install_dir'),root):raise ValueError('需要原位置的完整安装记录。')
    if probe(root):raise ValueError('请先关闭此安装的终端、启动面板和模型服务。')
    data=maintenance.journal(root);inventory=[];components=[];retained=[]
    if data:
        if data['status']=='completed':
            if fingerprint(state)!=data.get('new_state_sha256'):raise ValueError('维护后的安装记录发生变化，已保留备份。')
        elif data['status']=='rolled_back':
            clean=lambda value:{key:item for key,item in value.items() if key!='updated_at'}
            if clean(state)!=clean(data['old_state']):raise ValueError('回退后的安装记录发生变化，已保留备份。')
        folder=root/'.installer/updates'/data['id'];guard_path(folder)
        for name in maintenance._components(data):
            backup=folder/('backup-'+name);guard_path(backup)
            if not backup.exists():continue
            # Partial deletion after explicit discard may be resumed after a fresh preview.
            if not data.get('rollback_discarded') and maintenance._signature(root,backup)!=data['old_signatures'][name]:
                raise ValueError('回退备份已变化，已保留：'+name)
            entries=_tree(root,backup)
            if data.get('rollback_discarded'):
                previous=data.get('backup_cleanup',{})
                if previous.get('status')=='completed':raise ValueError('已清理位置又出现文件，已保留。')
                receipts={item['path']:item for item in previous.get('inventory',[])}
                for item in entries:
                    old=receipts.get(item['path'])
                    fields=('path','directory','size') if item['directory'] else ('path','directory','size','mtime')
                    if old is None or any(item[key]!=old.get(key) for key in fields):raise ValueError('剩余备份已变化，已保留。')
            components.append(name);inventory.extend(entries)
        if folder.exists():
            selected={Path(item['path']).parts[3] for item in inventory}
            retained=[p.name for p in sorted(folder.iterdir()) if p.name not in selected]
    result=dict(schema_version=1,install_dir=str(root),maintenance_id=data['id'] if data else None,
        maintenance_sha256=fingerprint(data),state_sha256=fingerprint(state),components=components,
        inventory=inventory,bytes_to_remove=sum(item['size'] for item in inventory),
        file_count=sum(not item['directory'] for item in inventory),retained_transaction_entries=retained,
        rollback_will_be_discarded=bool(inventory) and data['status']=='completed' and not data.get('rollback_discarded'))
    result['plan_sha256']=fingerprint(result);return result


def execute(root,expected,*,probe=running_processes):
    preview=plan(root,probe=probe)
    if preview['plan_sha256']!=expected:raise ValueError('备份或安装记录已变化，请重新查看后确认。')
    root=Path(preview['install_dir'])
    with writer_lock(root):
        current=plan(root,probe=probe)
        if current['plan_sha256']!=expected:raise ValueError('备份或安装记录已变化，请重新查看后确认。')
        if not current['inventory']:return dict(status='completed',removed_files=0,removed_bytes=0,rollback_discarded=False)
        data=maintenance.journal(root)
        # Durable before unlink: even a crash must not offer a partly erased rollback.
        data['rollback_discarded']=True
        receipts=data.get('backup_cleanup',{}).get('inventory',current['inventory'])
        data['backup_cleanup']=dict(status='running',plan_sha256=expected,removed_files=0,removed_bytes=0,error=None,inventory=receipts)
        if len(json.dumps(data,indent=2).encode('utf-8'))>3*1024*1024:raise ValueError('备份清单过大，已保留文件。')
        maintenance._save(root,data);result=data['backup_cleanup']
        try:
            files=[item for item in current['inventory'] if not item['directory']]
            directories=[item for item in current['inventory'] if item['directory']]
            for item in files:
                path=root/item['path']
                if _entry(root,path)!=item:raise ValueError('清理期间备份文件变化，已停止并保留剩余文件。')
                path.unlink();result['removed_files']+=1;result['removed_bytes']+=item['size']
            for item in sorted(directories,key=lambda item:len(Path(item['path']).parts),reverse=True):
                path=root/item['path'];guard_path(path)
                if root not in path.parents or not pathing.link_free(path):raise ValueError('备份路径超出清理范围。')
                path.rmdir() # Unlisted new files stop cleanup; never recursively delete.
            result['status']='completed';maintenance._save(root,data)
        except BaseException as error:
            result.update(status='failed',error=str(error));maintenance._save(root,data);raise
        return {**{key:item for key,item in result.items() if key!='inventory'},'rollback_discarded':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir',type=Path,required=True)
    parser.add_argument('--remove',action='store_true');parser.add_argument('--confirm')
    args=parser.parse_args(argv)
    try:
        if args.remove:
            if not args.confirm:raise ValueError('先查看清理范围，再提供 --confirm plan_sha256。')
            value=execute(args.install_dir,args.confirm)
        else:value={key:item for key,item in plan(args.install_dir).items() if key!='inventory'}
        print(json.dumps(value,indent=2,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('备份清理未完成：',error);return 1


if __name__=='__main__':raise SystemExit(main())
