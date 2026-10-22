"""Post-install launch boundaries, service ownership and terminal session lifecycle."""
from __future__ import annotations
import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from installer import start, launcher, workflow
from installer.checkpoint import Step, run_steps


class Tree:
    def __init__(self): self.attached=[]; self.closed=False
    def attach(self, process): self.attached.append(process)
    def close(self): self.closed=True


class StartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name); self.root=self.base/'installed'; self.workspace=self.base/'work'
        self.workspace.mkdir(); (self.root/'.installer').mkdir(parents=True)
        self.paths=workflow.paths_for(self.root)
        self.info=dict(paths=self.paths,settings={'python':'private-python','runtime_dir':str(self.paths['runtime'])},
                       model={'model':'fixture:1b','manifest_sha256':'a'*64},endpoint='http://127.0.0.1:11435')
        self.events=[]; self.trees=[]; self.reused=False
        self.print_patch=patch('builtins.print'); self.print_patch.start(); self.addCleanup(self.print_patch.stop)

    def tree(self):
        value=Tree(); self.trees.append(value); return value

    @contextlib.contextmanager
    def service(self,*args,**kwargs):
        self.events.append(('enter',args))
        try: yield {'reused':self.reused,'process':None}
        finally: self.events.append(('exit',None))

    def session(self,**kwargs):
        with patch.object(start.ollama_runtime,'require_windows'):
            return start.run_session(self.root,self.workspace,inspect=lambda root:self.info,service=self.service,
                check_service=lambda *args:self.events.append(('digest',None)),tree_factory=self.tree,**kwargs)

    def test_one_turn_uses_profile_and_cleans_service_on_success(self):
        turns=[]
        def turn(info,workspace,text,env):
            turns.append((workspace,text,env)); return 0
        with patch.dict(os.environ,{'ODESSY_MODEL_BACKEND':'cloud','ODESSY_TOOL_SERVER_URL':'http://remote.invalid','PYTHONPATH':'foreign'}):
            self.assertEqual(self.session(text='hello',turn=turn),0)
        self.assertEqual([e[0] for e in self.events],['enter','digest','exit'])
        env=turns[0][2]; self.assertEqual(env['ODESSY_CONFIG_FILE'],str(self.paths['config']))
        self.assertNotIn('ODESSY_TOOL_SERVER_URL',env); self.assertNotIn('ODESSY_MODEL_BACKEND',env)
        self.assertNotIn('PYTHONPATH',env); self.assertTrue(self.trees[0].closed)
        self.assertFalse(Path(env['ODESSY_COMMANDS_FILE']).parent.exists())

    def test_session_uses_bounded_thinking_budget_without_rewriting_profile(self):
        profile = self.paths['config']; profile.parent.mkdir(parents=True)
        profile.write_text('fixture profile unchanged', encoding='utf-8')
        received = []
        def turn(info, workspace, text, env):
            received.append(dict(env)); return 0
        with patch.dict(os.environ, {'ODESSY_MODEL_NUM_PREDICT': '1024'}):
            self.session(text='directory request', turn=turn)
        self.assertEqual(received[0]['ODESSY_MODEL_NUM_PREDICT'], '2048')
        self.assertEqual(profile.read_text(encoding='utf-8'), 'fixture profile unchanged')
        self.assertNotIn('ODESSY_DEBUG_MODEL_REPAIR', received[0])
        self.assertEqual(self.events[-1][0], 'exit')

    def test_interactive_exit_does_not_execute_a_request(self):
        with patch.object(start,'run_turn') as turn:
            self.assertEqual(self.session(read=lambda prompt:'/exit',turn=turn),0)
        turn.assert_not_called(); self.assertEqual(self.events[-1][0],'exit')

    def test_each_request_is_separate_and_failed_turn_can_retry(self):
        values=iter([' ', 'one','two','/quit']); calls=[]
        def turn(info,workspace,text,env): calls.append(text); return 1 if text=='one' else 0
        self.assertEqual(self.session(read=lambda prompt:next(values),turn=turn),0)
        self.assertEqual(calls,['one','two'])

    def test_ctrl_c_and_turn_failure_cleanup_owned_session(self):
        for failure in (KeyboardInterrupt(),ValueError('fixture failure')):
            self.events.clear()
            def turn(*args): raise failure
            with self.assertRaises(type(failure)): self.session(text='hello',turn=turn)
            self.assertEqual(self.events[-1][0],'exit'); self.assertTrue(self.trees[-1].closed)

    def test_reused_service_never_attached_to_owned_job(self):
        self.reused=True
        self.session(text='hello',turn=lambda *args:0)
        self.assertEqual(self.trees[0].attached,[])

    def test_digest_mismatch_never_executes_request_and_service_stops(self):
        with patch.object(start.ollama_runtime,'require_windows'), patch.object(start,'run_turn') as turn:
            with self.assertRaisesRegex(ValueError,'digest'):
                start.run_session(self.root,self.workspace,text='hello',inspect=lambda root:self.info,
                    service=self.service,check_service=lambda *args:(_ for _ in ()).throw(ValueError('digest mismatch')),
                    turn=turn,tree_factory=self.tree)
        turn.assert_not_called(); self.assertEqual(self.events[-1][0],'exit')

    def test_session_state_directories_are_unique_and_legacy_state_preserved(self):
        self.paths['service_state'].mkdir(); stale=self.paths['service_state']/'serve.lock'; stale.write_text('old owner')
        self.session(text='one',turn=lambda *args:0); self.session(text='two',turn=lambda *args:0)
        states=[event[1][2] for event in self.events if event[0]=='enter']
        self.assertNotEqual(states[0],states[1]); self.assertEqual(stale.read_text(),'old owner')

    def test_workspace_is_explicit_existing_and_separate(self):
        for folder in (self.root,self.root/'child',self.base,self.base/'missing',Path('relative')):
            with self.assertRaises((ValueError,OSError)): start.workspace_path(self.root,folder)
        self.assertEqual(start.workspace_path(self.root,self.workspace),self.workspace.resolve())

    def test_turn_preserves_argument_text_and_interactive_stdio(self):
        class Process:
            def wait(self,**kwargs): return 0
            def poll(self): return 0
        calls=[]
        def popen(command,**kwargs): calls.append((command,kwargs)); return Process()
        text='--hello 中文 & $x "quoted"'
        self.assertEqual(start.run_turn(self.info,self.workspace,text,{},popen=popen,tree_factory=self.tree),0)
        command,kwargs=calls[0]
        self.assertEqual(command[-2:],['--',text]); self.assertNotIn('stdin',kwargs); self.assertNotIn('shell',kwargs)
        self.assertTrue(self.trees[0].closed)

    def test_turn_kills_own_process_if_job_attach_fails(self):
        class BadTree(Tree):
            def attach(self,process): raise OSError('cannot attach')
        class Process:
            stopped=False
            def poll(self): return 0 if self.stopped else None
            def terminate(self): self.stopped=True
            def wait(self,**kwargs): return 0
        process=Process()
        with self.assertRaises(OSError): start.run_turn(self.info,self.workspace,'hello',{},popen=lambda *a,**k:process,tree_factory=BadTree)
        self.assertTrue(process.stopped)

    def test_completed_installation_identity_and_profile_checked(self):
        (self.root / '.installer').rmdir()
        plan=dict(install_dir=str(self.root),endpoint=self.info['endpoint'],model=self.info['model']['model'])
        run_steps(self.root,plan,[Step('fixture',lambda:{},lambda:{})])
        self.paths['config'].parent.mkdir(); self.paths['config'].write_text('ODESSY_DEPLOYMENT=foreign')
        settings=dict(runtime_dir=str(self.paths['runtime']),dependency_dir=str(self.paths['dependencies']))
        with patch.object(start,'check_runtime_access'),patch.object(start.connection,'inspect_application',return_value=settings),patch.object(start.model,'verify_model',return_value=self.info['model']):
            with self.assertRaisesRegex(ValueError,'profile'): start.inspect_installation(self.root)
        self.assertEqual(self.paths['config'].read_text(),'ODESSY_DEPLOYMENT=foreign')

    def test_incomplete_installation_rejected_before_inspecting_application(self):
        (self.root / '.installer').rmdir()
        with self.assertRaises(ValueError):
            run_steps(self.root,{},[Step('fixture',lambda:(_ for _ in ()).throw(ValueError('fixture failure')),lambda:None)])
        with patch.object(start.connection,'inspect_application') as inspect:
            with self.assertRaisesRegex(ValueError,'incomplete'): start.inspect_installation(self.root)
        inspect.assert_not_called()

    @unittest.skipUnless(os.name=='nt','The Windows launcher only targets Windows')
    def test_launch_console_command_never_uses_shell(self):
        (self.root / '.installer').rmdir()
        plan={'install_dir':str(self.root)}; run_steps(self.root,plan,[Step('fixture',lambda:{},lambda:{})])
        with patch.object(launcher.subprocess,'CREATE_NEW_CONSOLE',0x10,create=True):
            calls=[]; launcher.launch_console(self.root,self.workspace,popen=lambda *args,**kwargs:calls.append((args,kwargs)))
        self.assertNotIn('shell',calls[0][1]); self.assertEqual(calls[0][1]['creationflags'],0x10)
        self.assertIn(str(self.workspace.resolve()),calls[0][0][0]); self.assertIn('--pause-on-exit',calls[0][0][0])


class LauncherTkTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root=tk.Tk(); self.root.withdraw()
        except ImportError as error:self.skipTest(str(error))
        except tk.TclError as error:self.skipTest(str(error))
        self.process=SimpleNamespace(poll=lambda:None); self.calls=[]
        self.ui=launcher.Launcher(self.root,Path('D:/test-install'),launch=lambda *args:self.calls.append(args) or self.process)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        import gc
        self.ui.close()
        self.ui=None;self.root=None
        gc.collect()  # Do not leave Tcl variables for a later worker's GC cycle.

    def test_workspace_required_and_duplicate_launch_blocked(self):
        self.ui.start(); self.assertEqual(self.calls,[])
        self.ui.workspace.set('D:/work'); self.ui.start(); self.ui.start()
        self.assertEqual(len(self.calls),1); self.assertIn('disabled',self.ui.button.state())

    def test_closed_console_allows_another_launch(self):
        self.ui.workspace.set('D:/work'); self.ui.start(); self.process.poll=lambda:0
        self.ui.poll(schedule=False); self.assertNotIn('disabled',self.ui.button.state()); self.assertIsNone(self.ui.process)


if __name__=='__main__': unittest.main()
