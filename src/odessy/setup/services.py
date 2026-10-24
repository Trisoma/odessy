"""Explicit per-user services; native adapters never elevate privileges."""
from __future__ import annotations
from dataclasses import dataclass
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from .configuration import write_new
from .maintenance import atomic, guard, read_json

@dataclass(frozen=True)
class ServiceSpec:
    installation_id: str
    argv: tuple[str,...]
    working_directory: str

    def __post_init__(self):
        if not re.fullmatch('[a-z0-9-]{1,64}',self.installation_id): raise ValueError('invalid installation identity')
        if not self.argv or len(self.argv)>64 or any(not isinstance(a,str) or not a or any(c in a for c in '\r\n\0') for a in self.argv): raise ValueError('invalid service arguments')
        if not (Path(self.argv[0]).is_absolute() or PurePosixPath(self.argv[0]).is_absolute() or re.match(r'^[A-Za-z]:[/\\]',self.argv[0])): raise ValueError('service executable must be absolute')
        if not Path(self.working_directory).is_absolute() or any(c in self.working_directory for c in '\r\n\0'): raise ValueError('service working directory must be absolute')

    @property
    def name(self): return 'org.odessy.'+self.installation_id


def _systemd(value):
    # systemd has its own specifier/variable expansion, independent of a shell.
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def service_definition(spec,platform):
    if platform=='linux':
        text='[Unit]\nDescription=Odessy user runtime\n[Service]\nType=simple\nWorkingDirectory='+_systemd(spec.working_directory)+'\nExecStart='+' '.join(_systemd(a) for a in spec.argv)+'\nRestart=on-failure\nRestartSec=3\n[Install]\nWantedBy=default.target\n'
        return spec.name+'.service',text
    if platform=='macos':
        return spec.name+'.plist',plistlib.dumps({'Label':spec.name,'ProgramArguments':list(spec.argv),'WorkingDirectory':spec.working_directory,'RunAtLoad':True,'KeepAlive':False}).decode()
    if platform=='windows':
        ns='http://schemas.microsoft.com/windows/2004/02/mit/task'
        ET.register_namespace('',ns)
        def node(parent,name,text=None,**attrs):
            result=ET.SubElement(parent,'{'+ns+'}'+name,attrs)
            if text is not None: result.text=text
            return result
        task=ET.Element('{'+ns+'}Task',{'version':'1.2'})
        triggers=node(task,'Triggers');trigger=node(triggers,'LogonTrigger');node(trigger,'Enabled','true')
        principals=node(task,'Principals');principal=node(principals,'Principal',id='Author')
        user=os.environ.get('USERDOMAIN','')+'\\'+getpass.getuser() if os.environ.get('USERDOMAIN') else getpass.getuser()
        node(principal,'UserId',user);node(principal,'LogonType','InteractiveToken');node(principal,'RunLevel','LeastPrivilege')
        settings=node(task,'Settings');node(settings,'MultipleInstancesPolicy','IgnoreNew');node(settings,'ExecutionTimeLimit','PT0S');node(settings,'DisallowStartIfOnBatteries','false');node(settings,'StopIfGoingOnBatteries','false')
        actions=node(task,'Actions',Context='Author');execution=node(actions,'Exec');node(execution,'Command',spec.argv[0]);node(execution,'Arguments',subprocess.list2cmdline(spec.argv[1:]));node(execution,'WorkingDirectory',spec.working_directory)
        return spec.name+'.xml',ET.tostring(task,encoding='unicode')
    raise ValueError('unsupported user-service platform')


def native_run(argv):
    try:
        result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=True)
        return result.stdout.decode(errors='replace')[:65536]
    except (OSError,subprocess.SubprocessError): raise RuntimeError('user-service command failed; check native permissions/session and retry') from None

class ServiceManager:
    def __init__(self,root,*,platform=None,home=None,runner=native_run):
        self.root=guard(root)
        self.platform=platform or {'win32':'windows','darwin':'macos'}.get(sys.platform,'linux')
        self.home=guard(home or Path.home());self.runner=runner

    def path(self,spec):
        name,_=service_definition(spec,self.platform)
        if self.platform=='linux': return guard(self.home/'.config/systemd/user'/name)
        if self.platform=='macos': return guard(self.home/'Library/LaunchAgents'/name)
        return guard(self.root/name)

    def _expected(self,spec):
        name,content=service_definition(spec,self.platform)
        path=self.path(spec)
        return path,content,hashlib.sha256(content.encode()).hexdigest()

    def install(self,spec):
        path,content,sha=self._expected(spec);record_path=guard(self.root/'service.json')
        if record_path.exists():
            record=read_json(record_path)
            if record.get('sha256')!=sha or record.get('path')!=str(path): raise ValueError('another service is already recorded')
        elif path.exists(): raise ValueError('existing external service definition was preserved')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes()!=content.encode(): raise ValueError('service changed outside setup')
        # Record ownership before registration, so a failed registration is recoverable.
        atomic(record_path,{'schema_version':1,'path':str(path),'sha256':sha,'id':spec.installation_id,'argv':list(spec.argv),'working_directory':spec.working_directory,'platform':self.platform})
        if not path.exists(): write_new(path,content)
        try:
            if self.platform=='linux':
                self.runner(['systemctl','--user','daemon-reload']);self.runner(['systemctl','--user','enable',spec.name+'.service'])
            elif self.platform=='macos':
                self.runner(['launchctl','bootstrap','gui/'+str(os.getuid()),str(path)])
            else:
                # Never /F: a task registered by someone else is not ours to replace.
                self.runner(['schtasks.exe','/Create','/TN',spec.name,'/XML',str(path)])
        except Exception as exc: raise RuntimeError('service registration failed; definition retained for inspection/retry') from exc
        return {'registered':True,'started':self.platform=='macos','scope':'current-user','boot_before_login':False}

    def action(self,spec,action):
        if action not in ('start','stop','status','remove'): raise ValueError('unsupported service action')
        path,content,sha=self._expected(spec);record=read_json(self.root/'service.json')
        if record.get('path')!=str(path) or record.get('sha256')!=sha or record.get('id')!=spec.installation_id or not path.exists() or path.read_bytes()!=content.encode(): raise ValueError('service ownership changed; no native command was sent')
        if self.platform=='linux':
            if action=='remove':
                self.runner(['systemctl','--user','disable','--now',spec.name+'.service'])
                result='removed'
            else: result=self.runner(['systemctl','--user',{'status':'show'}.get(action,action),spec.name+'.service'])
        elif self.platform=='macos':
            target='gui/'+str(os.getuid())+'/'+spec.name
            verb={'start':'kickstart','stop':'kill','status':'print','remove':'bootout'}[action]
            result=self.runner(['launchctl',verb,*(['SIGTERM'] if action=='stop' else []),target])
        else:
            verb={'start':'/Run','stop':'/End','status':'/Query','remove':'/Delete'}[action]
            if action=='remove':
                # An idle task is already stopped. Query and stop conditionally;
                # do not turn the normal idle state into an uninstall failure.
                script=("$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskPath '\\' -TaskName '"+spec.name+"'; "
                        "if ($t.State -eq 'Running') { Stop-ScheduledTask -InputObject $t }; "
                        "Unregister-ScheduledTask -InputObject $t -Confirm:$false")
                result=self.runner(['powershell.exe','-NoProfile','-NonInteractive','-Command',script])
            else:
                result=self.runner(['schtasks.exe',verb,'/TN',spec.name])
        if action=='remove':
            path.unlink();(self.root/'service.json').unlink()
            if self.platform=='linux': self.runner(['systemctl','--user','daemon-reload'])
        return {'action':action,'native_output':result or '', 'scope':'current-user','boot_before_login':False}

    def load(self):
        data=read_json(self.root/'service.json')
        if data.get('platform')!=self.platform: raise ValueError('service record belongs to another platform')
        return ServiceSpec(data['id'],tuple(data['argv']),data['working_directory'])
