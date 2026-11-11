"""Single-layer, per-user Windows bootstrap. NSIS is a build-time dependency."""
import hashlib
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import threading
import uuid
import zipfile

#: Bound on the compiler. Solid LZMA over a ~180 MB payload legitimately takes many
#: minutes on a busy machine, so exceeding this is reported as "slow", never as a
#: compiler error, and the partial file is kept for inspection.
COMPILE_TIMEOUT = 5400


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
  # The payload root is passed explicitly: the Flutter setup only discovers a
  # payload beside the executable, and this bootstrap extracts it one level above
  # desktop/. The environment variable stays for the Python helper.
  Exec '"$INSTDIR\\desktop\\odessy_desktop.exe" --setup --payload "$INSTDIR"'
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


def _drained(command,log_path,*,timeout=COMPILE_TIMEOUT):
    """Run the compiler and consume its output; return ``(exit code, lines)``.

    ``makensis`` prints every packed file. When the caller's stdout is a pipe nobody
    reads (a build agent, a redirected job), that pipe fills and the compiler blocks
    forever on a write -- this is what produced an interrupted executable, not a slow
    compressor. The child's output is a pipe this function owns and drains on its own
    thread, and every line is mirrored into ``log_path`` so a stall or a failure can be
    diagnosed without re-running the build.
    """
    log_path=Path(log_path);log_path.parent.mkdir(parents=True,exist_ok=True)
    lines=[]
    with open(log_path,'w',encoding='utf-8',errors='replace') as log:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                 text=True,encoding='utf-8',errors='replace',bufsize=1)
        def drain():
            stream=process.stdout
            if stream is None:return
            for line in stream:
                lines.append(line.rstrip('\n'))
                log.write(line);log.flush()
        reader=threading.Thread(target=drain,name='nsis-output',daemon=True)
        reader.start()
        try:
            code=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill();process.wait()
            raise TimeoutError('NSIS compilation exceeded '+str(timeout)+'s; last output: '
                               +' | '.join(lines[-5:])) from None
        finally:
            reader.join(timeout=10)
    return code,lines


def compile_installer(source,output,*,compiler_path=None,log_path=None,timeout=COMPILE_TIMEOUT):
    """Compile one written ``.nsi`` and publish the artifact only after success.

    NSIS writes to ``<output>.incomplete``; it is renamed only when the compiler exited
    0 *and* the file starts with the PE signature. An interrupted or failed build
    therefore leaves a clearly named leftover and never a plausible installer.
    ``compiler_path`` may be a sequence (tests drive a script through its interpreter);
    production uses :func:`compiler` and the trusted local ``makensis.exe``.
    """
    source,output=Path(source),Path(output)
    partial=output.with_name(output.name+'.incomplete')
    partial.unlink(missing_ok=True)
    log=Path(log_path) if log_path else source.with_name(source.stem+'.makensis.log')
    chosen=compiler_path if compiler_path is not None else compiler()
    command=[chosen] if isinstance(chosen,str) else list(chosen)
    code,lines=_drained(command+['/V2',str(source)],log,timeout=timeout)
    if code!=0:
        raise RuntimeError('NSIS compilation failed with exit '+str(code)+' (log '+str(log)
                           +'); last output: '+' | '.join(lines[-3:]))
    if not partial.is_file() or partial.stat().st_size==0:
        raise RuntimeError('NSIS exited 0 but wrote no installer: '+str(partial))
    with open(partial,'rb') as stream:
        if stream.read(2)!=b'MZ':raise RuntimeError('NSIS output is not a Windows executable: '+str(partial))
    os.replace(partial,output)
    return output


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
    partial=Path(output).with_name(Path(output).name+'.incomplete')
    source.write_text(script(files,partial.resolve(),icon),encoding='utf-8-sig')
    return compile_installer(source,output,log_path=Path(stage)/'makensis.log')
