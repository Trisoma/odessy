"""Role-aware planning/proofs for the existing maintenance transaction engine."""
from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import subprocess
from installer import role_aware,workflow
from installer.components import application,connection,model
from installer.core.checkpoint import read_state,fingerprint
from installer.core.space import RESERVE_BYTES
from odessy.setup.contracts import SetupSelection


def content_inventory(root,entries):
    result=[]
    for entry in entries:
        if entry['directory']:continue
        path=root/entry['path'];model.guard_path(path)
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        result.append((entry['path'],h.hexdigest()))
    return result


def plan(root,bundle,executable=None,*,probe=None,folders=None,validate_base=True):
    from installer.operations import maintenance,uninstall
    for path in (root,bundle):model.guard_path(path)
    root,bundle=root.resolve(),bundle.resolve();maintenance.require_idle(root)
    if uninstall.removal_started(root):raise ValueError('uninstall already started')
    if (root/'.git').exists() or (root/'pyproject.toml').exists():raise ValueError('source checkout is not an installation')
    if probe and probe(root):raise ValueError('close installed processes before maintenance')
    state=read_state(root)
    if state['status']!='completed' or Path(state['plan']['install_dir']).resolve()!=root:raise ValueError('maintenance needs a completed owned installation')
    selection=SetupSelection.from_dict(state['plan']['selection'])
    new=role_aware.build_plan(root,Path(state['plan']['cache_dir']),bundle,selection)
    if validate_base:
        for step in workflow.steps_for(new)[:3]:
            if step.name in ('runtime','dependencies') and step.validate() is None:raise ValueError('base component needs repair')
    _,tool=application.validate_bundle(bundle)
    required=2*(sum(map(len,application.source_snapshot(workflow.REPO_ROOT).values()))+tool['unpacked_bytes'])+RESERVE_BYTES
    if shutil.disk_usage(root).free<required:raise ValueError('not enough space to preserve rollback copies')
    inventory=maintenance._tree(root,root/'application')
    value={'schema_version':1,'install_dir':str(root),'bundle':str(bundle),'executable':None,'executable_sha256':None,'state_sha256':fingerprint(state),'new_plan':new,'inventory':inventory,'file_hashes':content_inventory(root,inventory),'application_action':'repair-or-upgrade','tool_archive_sha256':tool['sha256'],'retained_shortcuts':[],'required_free_bytes':required,'reuse':['models','config','launcher'],'scope':'role-aware-core','activation_components':['application']}
    value['plan_sha256']=fingerprint(value);return value


def health(root,new):
    from installer.operations import component_repair
    validators={step.name:step.validate for step in workflow.steps_for(new)[:4]}
    repairs=[];issues={}
    for name in ('runtime','dependencies'):
        try:
            if validators[name]() is None:raise ValueError('owned component missing')
            if name=='dependencies':component_repair.dependency_integrity(root)
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            repairs.append(name);issues[name]=str(error)
    if 'runtime' in repairs and 'dependencies' not in repairs:
        repairs.append('dependencies');issues['dependencies']='rebuild environment at its pinned path with the runtime'
    return repairs,issues


def verify(root,app,state_dir,plan):
    # application.assemble already ran isolated CLI-help and fake-model/Node
    # smoke before returning. Inspect exact assembled hashes again; no provider.
    connection.inspect_application(app)
    return {'core_verified':True,'verification':'validated assembly with isolated fake-model/tool smoke','inference_verified':False,'model_contacted':False,'installation_ready':False}


def recovery_snapshot(root):
    from installer.operations import maintenance
    root=Path(root);model.guard_path(root)
    data=maintenance.journal(root)
    if not data or data['status']=='rolled_back' or data.get('rollback_discarded'):raise ValueError('no recoverable maintenance journal')
    folder=root/'.installer/updates'/data['id'];entries=[]
    for name in maintenance._components(data):
        for path in (root/name,folder/('backup-'+name)):
            entries.extend(maintenance._tree(root,path))
    return {'journal':data,'state':read_state(root),'inventory':entries,'file_hashes':content_inventory(root,entries)}
