"""Read-only audit of a Windows x64 Flutter desktop and optional ZIP (stdlib only).

PE layout: https://learn.microsoft.com/windows/win32/debug/pe-format
This verifies static imports, not LoadLibrary calls or clean-machine execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile


class PackageError(ValueError):
    pass


REQUIRED = {
    'odessy_desktop.exe', 'flutter_windows.dll', 'file_selector_windows_plugin.dll',
    'data/app.so', 'data/icudtl.dat', 'data/flutter_assets/AssetManifest.bin',
    'data/flutter_assets/FontManifest.json', 'data/flutter_assets/NOTICES.Z',
    'LICENSE', 'FONT-LICENSE.txt', 'README.txt',
}
VC_RUNTIME = {'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'}
WINDOWS_DLLS = set('''advapi32.dll bcrypt.dll crypt32.dll d3d9.dll dbghelp.dll
    dwmapi.dll dxgi.dll gdi32.dll imm32.dll iphlpapi.dll kernel32.dll ntdll.dll
    ole32.dll oleacc.dll oleaut32.dll opengl32.dll propsys.dll psapi.dll
    rpcrt4.dll shell32.dll shlwapi.dll uiautomationcore.dll user32.dll
    winmm.dll ws2_32.dll'''.split())


def require(condition, message):
    if not condition:
        raise PackageError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest().upper()


def relative_name(value):
    require(isinstance(value, str) and value, 'Empty/non-string package path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '\\' not in value and ':' not in value
            and all(part not in ('', '.', '..') for part in value.split('/')),
            f'Invalid package-relative path: {value}')
    for part in value.split('/'):
        stem = part.split('.', 1)[0].rstrip(' ').upper()
        require(not part.endswith(('.', ' '))
                and not any(ord(char) < 32 or char in '<>"|?*' for char in part)
                and stem not in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
                and not re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem),
                f'Invalid package-relative path: {value}')
    return value


def pe_imports(data):
    """Read AMD64 PE32+ ordinary and delay-load DLL imports without loading code."""
    def unpack(fmt, offset):
        require(0 <= offset <= len(data) - struct.calcsize(fmt), 'Truncated PE structure')
        return struct.unpack_from(fmt, data, offset)

    require(data[:2] == b'MZ', 'Native binary is not a PE image')
    pe, = unpack('<I', 0x3c)
    require(data[pe:pe + 4] == b'PE\0\0', 'Invalid PE signature')
    machine, sections = unpack('<HH', pe + 4)
    require(machine == 0x8664, 'Native binary is not Windows AMD64')
    optional_size, = unpack('<H', pe + 20)
    optional = pe + 24
    require(optional_size >= 112 and unpack('<H', optional)[0] == 0x20b,
            'Native binary is not PE32+')
    image_base, = unpack('<Q', optional + 24)
    headers_size, = unpack('<I', optional + 60)
    directory_count, = unpack('<I', optional + 108)
    require(112 + directory_count * 8 <= optional_size, 'Invalid PE directory count')
    section_table = optional + optional_size
    ranges = []
    for index in range(sections):
        virtual_size, address, raw_size, raw_offset = unpack('<IIII', section_table + index * 40 + 8)
        ranges.append((address, virtual_size, raw_size, raw_offset))

    def offset(rva, size):
        if 0 <= rva < headers_size and rva + size <= min(headers_size, len(data)):
            return rva
        for address, virtual_size, raw_size, raw_offset in ranges:
            delta = rva - address
            if 0 <= delta < max(virtual_size, raw_size):
                require(delta + size <= raw_size and raw_offset + delta + size <= len(data),
                        'PE RVA points outside file-backed section')
                return raw_offset + delta
        raise PackageError('PE RVA does not map to a section')

    def dll_name(rva):
        chars = bytearray()
        for index in range(260):
            char = data[offset(rva + index, 1)]
            if char == 0:
                name = chars.decode('ascii')
                require(re.fullmatch(r'[A-Za-z0-9_.-]+\.dll', name, re.I), 'Invalid DLL import name')
                return name.lower()
            chars.append(char)
        raise PackageError('Unterminated DLL name')

    result = {'machine': 'AMD64', 'imports': [], 'delay_imports': []}
    for directory, width, key in ((1, 20, 'imports'), (13, 32, 'delay_imports')):
        if directory_count <= directory:
            continue
        rva, size = unpack('<II', optional + 112 + directory * 8)
        if rva == 0 and size == 0:
            continue
        require(rva > 0 and size >= width, 'Invalid PE import directory')
        terminated = False
        for index in range(size // width):
            descriptor = unpack('<' + 'I' * (width // 4), offset(rva + index * width, width))
            if not any(descriptor):
                terminated = True
                break
            name_rva = descriptor[3] if directory == 1 else descriptor[1]
            if directory == 13 and not descriptor[0] & 1:
                name_rva -= image_base
            result[key].append(dll_name(name_rva))
        require(terminated, 'Unterminated PE import directory')
        result[key] = sorted(set(result[key]))
    return result


def audit_bundle(bundle):
    bundle = bundle.resolve(strict=True)
    manifest_data = (bundle / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_data.decode('utf-8-sig'))
    require(manifest['platform'] == 'windows-x64' and manifest['configuration'] == 'Release',
            'Expected Windows x64 Release manifest')
    payload = {}
    folded = set()
    for entry in manifest['payload']:
        name = relative_name(entry['path'])
        require(name.lower() not in folded and name.lower() != 'manifest.json',
                f'Duplicate/reserved payload path: {name}')
        folded.add(name.lower())
        path = bundle / name
        require(path.resolve().is_relative_to(bundle), f'Payload leaves bundle: {name}')
        require(path.is_file(), f'Missing payload file: {name}')
        data = path.read_bytes()
        require(len(data) == entry['bytes'] and sha256(data) == entry['sha256'].upper(),
                f'Payload size/hash mismatch: {name}')
        payload[name] = data
    require(REQUIRED <= payload.keys(), f'Required files missing from manifest: {sorted(REQUIRED - payload.keys())}')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    require(actual == set(payload) | {'manifest.json'}, 'Bundle has unmanifested files')
    for family in json.loads(payload['data/flutter_assets/FontManifest.json']):
        for font in family['fonts']:
            asset = 'data/flutter_assets/' + relative_name(font['asset'])
            require(asset in payload, f'Missing declared font: {asset}')

    native = {}
    prerequisites = set()
    for name, data in payload.items():
        if PurePosixPath(name).suffix.lower() not in ('.dll', '.exe'):
            continue
        details = pe_imports(data)
        classified = []
        for kind in ('imports', 'delay_imports'):
            for dependency in details[kind]:
                if dependency in folded:
                    location = 'bundle'
                elif dependency in VC_RUNTIME:
                    location = 'external VC++ prerequisite'
                    prerequisites.add(dependency)
                elif dependency in WINDOWS_DLLS or dependency.startswith(('api-ms-win-', 'ext-ms-win-')):
                    location = 'Windows DLL/API contract'
                else:
                    raise PackageError(f'Unresolved {kind} dependency in {name}: {dependency}')
                classified.append({'name': dependency, 'kind': kind, 'location': location})
        native[name] = {'machine': details['machine'], 'dependencies': classified}
    require('Visual C++' in payload['README.txt'].decode('utf-8-sig') or not prerequisites,
            'External VC++ prerequisites are not disclosed in README')
    report = {
        'passed': True, 'scope': 'static package audit', 'payload_files': len(payload),
        'native_binaries': native, 'external_vc_runtime': sorted(prerequisites),
        'self_contained': not prerequisites,
        'limitations': ['Does not load or execute DLLs', 'Does not verify runtime LoadLibrary paths',
                        'Does not establish clean-machine or OS-version compatibility',
                        'Manifest hashes establish consistency, not publisher authenticity'],
    }
    return report, payload | {'manifest.json': manifest_data}


def audit_archive(archive, payload, expected_sha256):
    actual_hash = sha256(archive.read_bytes())
    require(actual_hash == expected_sha256.upper(), 'Archive SHA-256 mismatch')
    with zipfile.ZipFile(archive) as zipped:
        files = {}
        folded = set()
        for info in zipped.infolist():
            name = relative_name(info.filename.rstrip('/'))
            require(name.lower() not in folded, f'Duplicate archive path: {name}')
            folded.add(name.lower())
            if not info.is_dir():
                files[name] = info
        manifests = [name for name in files if name.endswith('/manifest.json')]
        require(len(manifests) == 1, 'ZIP must have one top-level desktop folder/manifest')
        prefix = manifests[0][:-len('manifest.json')]
        require(prefix.count('/') == 1, 'ZIP must have one top-level desktop folder')
        require(set(files) == {prefix + name for name in payload}, 'Archive file set differs from bundle')
        for name, data in payload.items():
            require(zipped.read(files[prefix + name]) == data, f'Archive content mismatch: {name}')
    return {'bytes': archive.stat().st_size, 'sha256': actual_hash,
            'verified_files_including_manifest': len(payload)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report, payload = audit_bundle(args.bundle)
        if args.archive:
            checksum = Path(str(args.archive) + '.sha256').read_text(encoding='ascii').split()[0]
            require(re.fullmatch(r'[A-Fa-f0-9]{64}', checksum), 'Invalid SHA-256 sidecar')
            report['archive'] = audit_archive(args.archive, payload, checksum)
    except (OSError, ValueError, KeyError, TypeError, struct.error, zipfile.BadZipFile) as error:
        report = {'passed': False, 'error': str(error)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
