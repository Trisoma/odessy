"""The checkout desktop owns isolated state and never selects a real model."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from odessy.application import local_desktop


class LocalDesktopEntryTest(unittest.TestCase):
    def test_isolated_state_and_existing_identity_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor = root / 'build/local-desktop/bin/odessy-monitor.exe'
            link = root / 'build/odessy-link/odessy-link.exe'
            monitor.parent.mkdir(parents=True)
            link.parent.mkdir(parents=True)
            monitor.touch()
            link.touch()
            identity = root / 'build/local-desktop/state/link/identity/identity.dpapi'

            def initialize(*args, **kwargs):
                identity.parent.mkdir(parents=True, exist_ok=True)
                identity.touch()
                return type('Result', (), {'returncode': 0})()

            server = AsyncMock()
            with patch.dict(os.environ, {'ODESSY_API_TOKEN': 'fixture'}, clear=False), \
                    patch('sys.argv', ['desktop', '--root', str(root), '--port', '17777']), \
                    patch('subprocess.run', side_effect=initialize) as init, \
                    patch('odessy.application.serve.run_server', server):
                local_desktop.main()
                local_desktop.main()
                self.assertEqual(init.call_count, 1)
                self.assertEqual(os.environ['ODESSY_MONITORING'], 'on')
                self.assertEqual(os.environ['ODESSY_MODEL_NAME'], 'not-configured')
                self.assertEqual(os.environ['ODESSY_COLLABORATION_FILE'],
                                 str(root / 'build/local-desktop/state/collaboration.toml'))
                self.assertEqual(server.await_args.kwargs['backend'], 'lfm')
                self.assertTrue(server.await_args.kwargs['watch_stdin'])
                self.assertFalse(server.await_args.kwargs['print_token'])

    def test_missing_monitor_does_not_inherit_enabled_state(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'ODESSY_API_TOKEN': 'fixture', 'ODESSY_MONITORING': 'on'}, clear=False), \
                patch('sys.argv', ['desktop', '--root', directory, '--port', '17777']), \
                patch('odessy.application.serve.run_server', AsyncMock()):
            local_desktop.main()
            self.assertEqual(os.environ['ODESSY_MONITORING'], 'off')


if __name__ == '__main__':
    unittest.main()
