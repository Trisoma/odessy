import subprocess
from pathlib import Path
import pytest
from odessy.setup.services import ServiceSpec, service_definition, ServiceManager

@pytest.mark.parametrize('platform', ['windows','linux','macos'])
def test_service_definition_is_user_scoped_and_quotes_paths(platform, tmp_path):
    spec=ServiceSpec('test-install', ('C:/Program Files/Python/python.exe','-m','odessy','serve'), str(tmp_path))
    name, content=service_definition(spec, platform)
    assert 'test-install' in name
    assert 'C:/Program Files/Python/python.exe' in content
    assert 'sudo' not in content and 'SYSTEM' not in content
    if platform=='windows': assert 'InteractiveToken' in content and 'LeastPrivilege' in content
    if platform=='linux': assert '[Service]' in content and 'WantedBy=default.target' in content
    if platform=='macos': assert '<key>ProgramArguments</key>' in content


def test_external_or_changed_service_is_never_controlled(tmp_path):
    calls=[]
    manager=ServiceManager(tmp_path, platform='linux', home=tmp_path/'home', runner=lambda argv: calls.append(argv))
    spec=ServiceSpec('test-install', ('/usr/bin/python3','-m','odessy','serve'), str(tmp_path))
    target=manager.path(spec)
    target.parent.mkdir(parents=True)
    target.write_text('external')
    with pytest.raises(ValueError): manager.install(spec)
    assert calls==[] and target.read_text()=='external'
    target.unlink()
    manager.install(spec)
    target.write_text('changed')
    with pytest.raises(ValueError): manager.action(spec,'stop')


def test_permission_failure_is_reported_and_retry_is_safe(tmp_path):
    def fail(argv): raise subprocess.CalledProcessError(1,argv)
    manager=ServiceManager(tmp_path, platform='linux', home=tmp_path/'home',runner=fail)
    spec=ServiceSpec('test-install', ('/usr/bin/python3','-m','odessy','serve'), str(tmp_path))
    with pytest.raises(RuntimeError): manager.install(spec)
    assert manager.path(spec).exists()
    calls=[]; manager.runner=lambda argv: calls.append(argv)
    manager.install(spec)
    assert calls and '--user' in calls[0]


def test_service_rejects_shell_expansions_and_invalid_ids(tmp_path):
    for argv in [('/bin/python','bad\nargument'), ('relative-python',)]:
        with pytest.raises(ValueError): ServiceSpec('test',argv,str(tmp_path))
    with pytest.raises(ValueError): ServiceSpec('../outside',('/bin/python',),str(tmp_path))


def test_windows_idle_task_removal_does_not_require_end_success(tmp_path):
    calls=[]
    manager=ServiceManager(tmp_path,platform='windows',runner=lambda argv:calls.append(argv))
    spec=ServiceSpec('test-install',('C:/Python/python.exe','-m','odessy','serve'),str(tmp_path))
    manager.install(spec)
    manager.action(spec,'remove')
    assert not any('/End' in call for call in calls)
    script=calls[-1][-1]
    assert 'Unregister-ScheduledTask' in script and "State -eq 'Running'" in script
    assert not manager.path(spec).exists()
