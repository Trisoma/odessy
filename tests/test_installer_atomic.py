"""Atomic replacement keeps original records through transient and permanent locks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import atomic


class AtomicReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'pending';self.target=self.root/'record'
        self.source.write_bytes(b'new');self.target.write_bytes(b'original')

    def test_transient_windows_lock_is_retried_without_losing_the_original(self):
        original=atomic.os.replace;attempts=[]
        def locked(source,target):
            attempts.append(1)
            self.assertEqual(self.target.read_bytes(),b'original')
            if len(attempts)<3:
                error=PermissionError('temporarily locked');error.winerror=32;raise error
            original(source,target)
        with patch.object(atomic.os,'replace',side_effect=locked),patch.object(atomic.time,'sleep'):
            atomic.replace(self.source,self.target)
        self.assertEqual(self.target.read_bytes(),b'new');self.assertFalse(self.source.exists())
        self.assertEqual(len(attempts),3)

    def test_persistent_denial_is_bounded_and_preserves_both_files(self):
        error=PermissionError('still denied');error.winerror=5
        with patch.object(atomic.os,'replace',side_effect=error) as attempt,patch.object(atomic.time,'sleep'):
            with self.assertRaises(PermissionError):atomic.replace(self.source,self.target)
        self.assertEqual(attempt.call_count,6)
        self.assertEqual(self.target.read_bytes(),b'original');self.assertEqual(self.source.read_bytes(),b'new')

    def test_other_errors_are_reported_without_retry(self):
        with patch.object(atomic.os,'replace',side_effect=FileNotFoundError('missing')) as attempt:
            with self.assertRaises(FileNotFoundError):atomic.replace(self.source,self.target)
        self.assertEqual(attempt.call_count,1)


if __name__=='__main__':unittest.main()
