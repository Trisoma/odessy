"""Role-aware composition of the existing installer stages; no model download."""
from __future__ import annotations
import json
import os
from pathlib import Path
from installer import workflow
from installer.core.checkpoint import Step,run_steps
from installer.core.space import load_resources
from installer.components import runtime,dependencies,application,model
from odessy.setup.contracts import SetupSelection,public
from odessy.setup.configuration import write_new,environment_text
from odessy.setup.planning import build_plan as settings_plan


def build_plan(root,cache,bundle,selection):
    if selection.platform!='windows' or selection.architecture!='x86_64':raise ValueError('existing installer engine supports Windows x64 only')
    plan=workflow.build_plan(Path(root),Path(cache),Path(bundle),'http://127.0.0.1:11435',180)
    runtime_manifest,runtime_resources=runtime.load_release(runtime.DEFAULT_RESOURCES)
    dependency_manifest,dependency_resources=load_resources(dependencies.DEFAULT_RESOURCES)
    _,tool=application.validate_bundle(Path(bundle))
    sources=application.source_snapshot(workflow.REPO_ROOT)
    app_bytes=sum(item['unpacked_bytes'] for item in runtime_resources['runtimes'].values())+sum(item['unpacked_bytes'] for item in dependency_resources['layout'].values())+tool['unpacked_bytes']+sum(map(len,sources.values()))
    plan.update(engine='role-aware-existing-installer-v1',selection=public(selection),model=None,model_strategy='external' if selection.model else 'unconfigured',
                budget_bytes={'cache':runtime_manifest.download_bytes+2*dependency_manifest.download_bytes,'application':2*app_bytes,'models':0},
                downloads=[{'id':artifact.id,'size_bytes':artifact.size_bytes,'sha256':artifact.sha256,'url':artifact.url} for manifest in (runtime_manifest,dependency_manifest) for artifact in manifest.artifacts])
    return plan


def steps_for(plan,*,cancelled=lambda:False,emit_progress=lambda value:None):
    # Original runtime preparation, verified downloads, dependency environment,
    # application assembly and validators. Never call the pinned model stages.
    core=workflow.steps_for(plan,cancelled=cancelled,emit_progress=emit_progress)[:4]
    root=Path(plan['install_dir']);selection=SetupSelection.from_dict(plan['selection'])
    contents={'selection.json':json.dumps({'schema_version':1,'selection':public(selection)},indent=2)+'\n'}
    if selection.model:contents['odessy.env']=environment_text(settings_plan(selection))
    def validate():
        for name,content in contents.items():
            path=root/'config'/name;model.guard_path(path)
            if not path.exists():return None
            if path.read_bytes()!=content.encode():raise ValueError('existing configuration differs; it was preserved')
        return {'configuration_saved':True,'model_configured':selection.model is not None,'voice_qualified':False}
    def prepare():
        (root/'config').mkdir(exist_ok=True)
        for name,content in contents.items():
            target=root/'config'/name;model.guard_path(target)
            if not target.exists():write_new(target,content)
            elif target.read_bytes()!=content.encode():raise ValueError('existing configuration differs; it was preserved')
        return validate()
    return [*core,Step('configuration',prepare,validate)]


def revalidate(plan):
    workflow.ollama_runtime.require_windows()
    expected=build_plan(plan['install_dir'],plan['cache_dir'],plan['tool_bundle'],SetupSelection.from_dict(plan['selection']))
    if expected!=plan:raise ValueError('installer release or role selection changed after review')
    from installer.operations.maintenance import require_idle
    from installer.operations.uninstall import removal_started
    root=Path(plan['install_dir']);require_idle(root)
    if removal_started(root):raise ValueError('uninstall has started; choose a new installation root')
    if not all(volume['sufficient'] for volume in workflow.combined_budget(plan)):raise ValueError('insufficient disk space')


def execute(plan,*,resume=False,cancelled=lambda:False,emit=lambda event:None):
    revalidate(plan)
    return run_steps(Path(plan['install_dir']),plan,steps_for(plan,cancelled=cancelled),resume=resume,emit=emit,cancelled=cancelled)
