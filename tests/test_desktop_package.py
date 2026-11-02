"""Package rejection/qualification regressions; fixtures use isolated temp storage."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location(
    'desktop_package', Path(__file__).parents[1] / 'tool/check_desktop_package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def pe(imports=(), delay=(), machine=0x8664):
    data = bytearray(4096)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, machine, 1)
    struct.pack_into('<H', data, 148, 240)
    optional = 152
    struct.pack_into('<H', data, optional, 0x20b)
    struct.pack_into('<Q', data, optional + 24, 0x140000000)
    struct.pack_into('<I', data, optional + 60, 512)
    struct.pack_into('<I', data, optional + 108, 16)
    struct.pack_into('<IIII', data, optional + 240 + 8, 3584, 0x1000, 3584, 512)
    for directory, rva, width, names, string_rva in (
        (1, 0x1000, 20, imports, 0x1300), (13, 0x1100, 32, delay, 0x1500),
    ):
        if not names:
            continue
        struct.pack_into('<II', data, optional + 112 + directory * 8,
                         rva, width * (len(names) + 1))
        for index, name in enumerate(names):
            pointer = string_rva + index * 128
            descriptor = [0] * (width // 4)
            if directory == 1:
                descriptor[3] = pointer
            else:
                descriptor[0:2] = [1, pointer]
            struct.pack_into('<' + 'I' * len(descriptor), data,
                             512 + rva - 0x1000 + index * width, *descriptor)
            encoded = name.encode('ascii') + b'\0'
            data[512 + pointer - 0x1000:512 + pointer - 0x1000 + len(encoded)] = encoded
    return bytes(data)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='odessy-package-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'preview'
        self.bundle.mkdir()
        for name in package.REQUIRED:
            self.write(name, b'fixture')
        self.write('README.txt', b'Requires Microsoft Visual C++ x64 runtime.')
        self.write('odessy_desktop.exe', pe(('flutter_windows.dll', 'MSVCP140.dll')))
        self.write('flutter_windows.dll', pe(('KERNEL32.dll',), ('dbghelp.dll',)))
        self.write('file_selector_windows_plugin.dll', pe(('flutter_windows.dll',)))
        self.write('data/app.so', b'\x7fELFopaque-AOT-data')
        self.write('data/flutter_assets/FontManifest.json', json.dumps([
            {'family': 'Fixture', 'fonts': [{'asset': 'fonts/test.ttf'}]}
        ]).encode())
        self.write('data/flutter_assets/fonts/test.ttf', b'fixture-font')
        self.manifest()

    def write(self, name, data):
        path = self.bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def manifest(self):
        entries = []
        for path in sorted(self.bundle.rglob('*')):
            if path.is_file() and path.name != 'manifest.json':
                data = path.read_bytes()
                entries.append({'path': path.relative_to(self.bundle).as_posix(),
                                'bytes': len(data), 'sha256': package.sha256(data)})
        data = {'platform': 'windows-x64', 'configuration': 'Release', 'payload': entries}
        self.write('manifest.json', json.dumps(data).encode())

    def assert_rejected(self, message):
        with self.assertRaisesRegex(package.PackageError, message):
            package.audit_bundle(self.bundle)

    def archive(self, contents):
        path = self.root / 'preview.zip'
        with zipfile.ZipFile(path, 'w') as zipped:
            for name, data in contents.items():
                zipped.writestr('preview/' + name, data)
        return path, package.sha256(path.read_bytes())

    def test_reads_ordinary_and_delay_imports(self):
        parsed = package.pe_imports(pe(('KERNEL32.dll',), ('dbghelp.dll',)))
        self.assertEqual(parsed, {'machine': 'AMD64', 'imports': ['kernel32.dll'],
                                  'delay_imports': ['dbghelp.dll']})

    def test_wrong_architecture_rejected(self):
        self.write('odessy_desktop.exe', pe(machine=0xaa64))
        self.manifest()
        self.assert_rejected('not Windows AMD64')

    def test_truncated_pe_rejected(self):
        with self.assertRaisesRegex(package.PackageError, 'Truncated'):
            package.pe_imports(pe()[:18])

    def test_unmapped_import_name_rejected(self):
        data = bytearray(pe(('kernel32.dll',)))
        struct.pack_into('<I', data, 512 + 12, 0xdeadbeef)
        with self.assertRaisesRegex(package.PackageError, 'RVA'):
            package.pe_imports(bytes(data))

    def test_external_runtime_is_disclosed_not_self_contained(self):
        report, _ = package.audit_bundle(self.bundle)
        self.assertEqual(report['external_vc_runtime'], ['msvcp140.dll'])
        self.assertFalse(report['self_contained'])
        self.assertEqual(len(report['native_binaries']), 3)

    def test_missing_dll_rejected(self):
        (self.bundle / 'flutter_windows.dll').unlink()
        self.assert_rejected('Missing payload file')

    def test_changed_aot_payload_rejected(self):
        self.write('data/app.so', b'changed')
        self.assert_rejected('size/hash mismatch')

    def test_omitted_license_rejected_even_with_updated_manifest(self):
        (self.bundle / 'FONT-LICENSE.txt').unlink()
        self.manifest()
        self.assert_rejected('Required files missing')

    def test_missing_declared_font_rejected(self):
        (self.bundle / 'data/flutter_assets/fonts/test.ttf').unlink()
        self.manifest()
        self.assert_rejected('Missing declared font')

    def test_unmanifested_files_rejected(self):
        self.write('leftover.log', b'scratch')
        self.assert_rejected('unmanifested files')

    def test_case_duplicate_payload_rejected(self):
        path = self.bundle / 'manifest.json'
        manifest = json.loads(path.read_bytes())
        entry = manifest['payload'][0].copy()
        entry['path'] = entry['path'].upper()
        manifest['payload'].append(entry)
        path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assert_rejected('Duplicate/reserved')

    def test_unknown_delay_dependency_rejected(self):
        self.write('flutter_windows.dll', pe(delay=('missing-plugin.dll',)))
        self.manifest()
        self.assert_rejected('Unresolved delay_imports')

    def test_runtime_prerequisite_requires_readme_notice(self):
        self.write('README.txt', b'Preview')
        self.manifest()
        self.assert_rejected('not disclosed')

    def test_matching_archive_passes(self):
        _, contents = package.audit_bundle(self.bundle)
        path, digest = self.archive(contents)
        report = package.audit_archive(path, contents, digest)
        self.assertEqual(report['verified_files_including_manifest'], len(contents))

    def test_archive_content_differs_despite_valid_archive_hash(self):
        _, contents = package.audit_bundle(self.bundle)
        path, digest = self.archive(contents | {'LICENSE': b'changed'})
        with self.assertRaisesRegex(package.PackageError, 'content mismatch'):
            package.audit_archive(path, contents, digest)

    def test_archive_missing_file_rejected(self):
        _, contents = package.audit_bundle(self.bundle)
        changed = contents.copy()
        del changed['data/icudtl.dat']
        path, digest = self.archive(changed)
        with self.assertRaisesRegex(package.PackageError, 'file set differs'):
            package.audit_archive(path, contents, digest)

    def test_archive_windows_case_collision_rejected(self):
        _, contents = package.audit_bundle(self.bundle)
        path, digest = self.archive(contents | {'license': contents['LICENSE']})
        with self.assertRaisesRegex(package.PackageError, 'Duplicate archive path'):
            package.audit_archive(path, contents, digest)

    def test_windows_unsafe_paths_rejected(self):
        for name in ('CON', 'data/aux.txt', 'COM1.log', 'lpt9/file',
                     'data/trailing.', 'data/trailing ', 'data/a?b',
                     'data/a\x01b', 'data/a|b'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(package.PackageError, 'Invalid package'):
                    package.relative_name(name)

    def test_windows_non_reserved_names_remain_valid(self):
        for name in ('data/console.txt', 'data/com10.log', 'data/auxiliary.txt'):
            with self.subTest(name=name):
                self.assertEqual(package.relative_name(name), name)

    def test_archive_windows_reserved_path_rejected(self):
        _, contents = package.audit_bundle(self.bundle)
        path, digest = self.archive(contents | {'data/CON.txt': b'fixture'})
        with self.assertRaisesRegex(package.PackageError, 'Invalid package'):
            package.audit_archive(path, contents, digest)

    def test_archive_checksum_mismatch_rejected(self):
        _, contents = package.audit_bundle(self.bundle)
        path, _ = self.archive(contents)
        with self.assertRaisesRegex(package.PackageError, 'SHA-256 mismatch'):
            package.audit_archive(path, contents, '0' * 64)


if __name__ == '__main__':
    unittest.main()
