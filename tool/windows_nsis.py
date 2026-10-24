"""Single-layer, per-user Windows bootstrap. NSIS is a build-time dependency."""
import hashlib
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import uuid
import zipfile


def validate_members(entries):
    if len(entries)>100000:raise ValueError('too many payload members')
    seen=set();total=0
    for entry in entries:
        name=entry.filename.rstrip('/')
        parts=name.split('/')
        if (not name or name.startswith('/') or '\\' in name or ':' in name
                or any(ord(c)<32 for c in name) or any(part in ('','..','.')
                or part.rstrip(' .')!=part or PureWindowsPath(part).is_reserved() for part in parts)
                or (entry.external_attr>>16)&0o170000==0o120000):
            raise ValueError('unsafe Windows payload path')
        if name.casefold() in seen:raise ValueError('duplicate Windows payload path')
        seen.add(name.casefold());total+=entry.file_size
        if entry.file_size>2*1024**3 or total>4*1024**3:raise ValueError('payload exceeds extraction limit')


def quote(value):
    value=str(value)
    if any(c in value for c in '\r\n\0'):raise ValueError('invalid NSIS path')
    return value.replace('$','$$').replace('"','$\\"')


def script(files,output,icon):
    return f'''Unicode true
RequestExecutionLevel user
Name "Odessy Setup"
Caption "Preparing Odessy Setup"
OutFile "{quote(output)}"
Icon "{quote(icon)}"
CRCCheck force
SetCompressor /SOLID lzma
AutoCloseWindow true
ShowInstDetails show
Page instfiles
Section "Preparing Odessy Setup"
  SetShellVarContext current
  System::Call 'kernel32::GetCurrentProcessId() i .r0'
  System::Call 'kernel32::GetTickCount() i .r1'
  StrCpy $INSTDIR "$LOCALAPPDATA\\Odessy\\Setup\\{uuid.uuid4().hex}-$0-$1"
  IfFileExists "$INSTDIR\\." 0 +3
    MessageBox MB_ICONSTOP "Setup staging already exists. Reopen the installer."
    Abort
  SetOutPath "$INSTDIR"
  DetailPrint "Preparing verified Odessy components..."
  File /r "{quote(Path(files)/'*')}"
  IfErrors failed
  System::Call 'kernel32::SetEnvironmentVariableW(w "ODESSY_SETUP_PAYLOAD", w "$INSTDIR") i .r0'
  HideWindow
  Exec '"$INSTDIR\\desktop\\odessy_desktop.exe" --setup'
  IfErrors failed
  Goto done
failed:
  MessageBox MB_ICONSTOP "Odessy Setup could not start. Prepared files were retained for inspection."
  SetErrorLevel 1
done:
SectionEnd
'''


def compiler():
    candidate=os.environ.get('ODESSY_MAKENSIS') or shutil.which('makensis')
    if candidate:return str(Path(candidate).resolve())
    root=Path(__file__).resolve().parents[2]/'.tooling/nsis-3.13'
    candidates=list(root.glob('*/makensis.exe'))
    if candidates:return str(candidates[0])
    raise RuntimeError('NSIS is required: set ODESSY_MAKENSIS to the trusted makensis.exe; no IExpress fallback')


def build(payload,output,stage):
    try:
        from tool.audit_setup_artifact import audit
    except ModuleNotFoundError:
        from audit_setup_artifact import audit
    with zipfile.ZipFile(payload) as archive:validate_members(archive.infolist())
    audit(payload)  # All archive paths and bytes must match the reviewed manifest.
    files=Path(stage)/'files';files.mkdir()
    with zipfile.ZipFile(payload) as archive:
        if 'runtime/python.exe' not in archive.namelist() or 'desktop/odessy_desktop.exe' not in archive.namelist():
            raise ValueError('Windows payload needs desktop release and bundled Python')
        archive.extractall(files)
    icon=Path(__file__).resolve().parents[1]/'installer/assets/odessy-boat.ico'
    source=Path(stage)/'OdessySetup.nsi'
    source.write_text(script(files,Path(output).resolve(),icon),encoding='utf-8-sig')
    subprocess.run([compiler(),'/V2',str(source)],check=True,timeout=600)
    if not Path(output).is_file():raise RuntimeError('NSIS did not produce an installer')
    return Path(output)
