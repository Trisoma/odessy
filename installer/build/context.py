"""Package the existing installer plus its reviewed source/resource inputs."""
from pathlib import Path


def files(repo):
    repo=Path(repo)
    from installer.components.application import source_snapshot
    from installer.components.toolbundle import source_files
    result={}
    for path in (repo/'installer').rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix not in ('.py','.json','.txt'):continue
        if path.is_symlink():raise ValueError('installer source link refused')
        result[path.relative_to(repo).as_posix()]=path.read_bytes()
    snapshot=source_snapshot(repo)
    # A build must never redistribute local credentials or model preferences.
    snapshot['config/odessy.env']=b'# Configure an explicit model before running the agent.\n'
    for name,data in snapshot.items():result['installer/_payload/'+name]=data
    for path in source_files(repo):result['installer/_payload/'+path.relative_to(repo).as_posix()]=path.read_bytes()
    return result


def write(repo,destination):
    for name,data in files(repo).items():
        path=Path(destination)/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
