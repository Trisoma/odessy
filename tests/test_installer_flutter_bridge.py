from pathlib import Path
import pytest
from odessy.setup import bridge


def test_flutter_request_reaches_existing_engine(monkeypatch):
    from installer import flutter_bridge
    calls=[]
    monkeypatch.setattr(flutter_bridge,'execute',lambda params:calls.append(params) or {'engine':'installer.workflow'})
    result=bridge.execute({'id':'engine-1','operation':'installer','parameters':{'action':'status','root':'C:/Odessy'}})
    assert result['engine']=='installer.workflow' and len(calls)==1


def test_role_adapter_uses_original_steps_and_never_model_steps(tmp_path,monkeypatch):
    from installer import role_aware,workflow
    from installer.core.checkpoint import Step
    calls=[]
    steps=[Step(name,lambda name=name:calls.append(name) or {'ok':True},lambda:None) for name in workflow.STEP_NAMES]
    monkeypatch.setattr(workflow,'steps_for',lambda *args,**kwargs:steps)
    plan={'install_dir':str(tmp_path/'new'),'selection':{'role':'execution'},'cache_dir':str(tmp_path/'cache'),'tool_bundle':str(tmp_path/'bundle')}
    selected=role_aware.steps_for(plan)
    assert [step.name for step in selected]==['tool_bundle','runtime','dependencies','application','configuration']
    assert all(step.name not in ('model','ollama_runtime','connection') for step in selected)


def test_cancellation_and_resume_use_existing_checkpoint_engine(tmp_path,monkeypatch):
    from installer import role_aware
    from installer.core.checkpoint import Step,read_state
    plan={'install_dir':str(tmp_path/'new'),'selection':{'role':'execution'}}
    monkeypatch.setattr(role_aware,'revalidate',lambda value:None)
    done=[]
    step=Step('runtime',lambda:done.append('installed') or {'ok':True},lambda:{'ok':True} if done else None)
    monkeypatch.setattr(role_aware,'steps_for',lambda *args,**kwargs:[step])
    with pytest.raises(KeyboardInterrupt):role_aware.execute(plan,cancelled=lambda:True)
    assert read_state(Path(plan['install_dir']))['status']=='cancelled'
    result=role_aware.execute(plan,resume=True)
    assert result['summary']['workflow_complete'] and done==['installed']


def test_cancel_marker_is_forwarded_without_engine_execution(monkeypatch,tmp_path):
    from installer import flutter_bridge
    marker=tmp_path/'odessy-setup-test'/'cancel';marker.parent.mkdir();marker.touch()
    monkeypatch.setattr(flutter_bridge,'execute',lambda parameters,**kwargs:{'cancelled':kwargs['cancelled']()})
    result=bridge.execute({'id':'cancel-1','operation':'installer','parameters':{'action':'status','root':'C:/Odessy'},'cancel_file':str(marker)})
    assert result['cancelled'] is True
