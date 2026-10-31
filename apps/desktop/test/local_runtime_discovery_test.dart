import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';
import 'package:odessy_desktop/app/setup/disconnected_api.dart';
import 'package:odessy_desktop/app/session_controller.dart';

void main() {
  test('direct checkout build discovers its own source and virtual environment',
      () {
    final root =
        Directory.systemTemp.createTempSync('odessy-runtime-discovery-');
    addTearDown(() => root.deleteSync(recursive: true));
    File('${root.path}/pyproject.toml')
        .writeAsStringSync('[project]\nname = "odessy"\n');
    final bridge = File('${root.path}/src/odessy/setup/bridge.py');
    bridge.parent.createSync(recursive: true);
    bridge.writeAsStringSync('');
    final python = File(
        '${root.path}/.venv/${Platform.isWindows ? 'Scripts/python.exe' : 'bin/python3'}');
    python.parent.createSync(recursive: true);
    python.writeAsStringSync('');
    final executable = File(
        '${root.path}/apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe');
    executable.parent.createSync(recursive: true);
    executable.writeAsStringSync('');
    final backend = LocalSetupBackend(executablePath: executable.path);
    expect(Directory(backend.sourceRoot).absolute.path, root.absolute.path);
    expect(File(backend.python).absolute.path, python.absolute.path);
  });

  test('unrelated parent directories are not adopted as a source runtime', () {
    final root = Directory.systemTemp.createTempSync('other-app-runtime-');
    addTearDown(() => root.deleteSync(recursive: true));
    File('${root.path}/pyproject.toml')
        .writeAsStringSync('[project]\nname = "other"\n');
    final backend = LocalSetupBackend(executablePath: '${root.path}/app.exe');
    expect(backend.sourceRoot, isEmpty);
  });

  test('intentionally disconnected startup is not a failed connection attempt',
      () async {
    final session = DesktopSession(DisconnectedApi());
    addTearDown(session.dispose);
    await session.start();
    expect(session.connected, isFalse);
    expect(session.error, isEmpty);
    expect(session.status, 'Not connected');
  });
}
