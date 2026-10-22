"""Windows 8.3 short names must never be mistaken for links or other paths."""
import ctypes
import os
import tempfile
import unittest
from pathlib import Path

from installer import pathing, uninstall


def short_name(path: Path):
    """The 8.3 alias Windows generated for this directory, if it has one."""
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    if not length or buffer.value == str(path):
        return None
    return Path(buffer.value)


class PathingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'odessy-short-name-regression'
        self.base.mkdir()
        if os.name != 'nt':
            self.skipTest('8.3 short names only exist on Windows')
        self.short = short_name(self.base)
        if self.short is None:
            self.skipTest('This volume does not generate 8.3 short names')

    def test_short_and_long_spelling_are_the_same_location(self):
        self.assertTrue(pathing.same(self.short, self.base))
        self.assertTrue(pathing.same(self.base, self.short))
        self.assertFalse(pathing.same(self.short, self.base / 'child'))

    def test_short_spelling_is_not_a_reparse_point(self):
        self.assertTrue(pathing.link_free(self.short))
        self.assertTrue(pathing.link_free(self.short / 'not-created-yet'))

    def test_tree_accepts_a_root_spelled_with_a_short_name(self):
        folder = self.base / 'application'; folder.mkdir()
        (folder / 'payload.bin').write_bytes(b'fixture')
        entries = uninstall._tree(self.short, self.short / 'application')
        self.assertEqual([entry['path'] for entry in entries], ['application', 'application/payload.bin'])


if __name__ == '__main__': unittest.main()
