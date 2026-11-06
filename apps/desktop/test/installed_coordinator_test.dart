import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';

void main() {
  test('a missing workspace never blocks the installed launch', () {
    expect(InstalledCoordinator.launchDirectory(''), isNull);
    expect(
        InstalledCoordinator.launchDirectory(
            '${Directory.systemTemp.path}${Platform.pathSeparator}absent-odessy-workspace'),
        isNull);
    expect(InstalledCoordinator.launchDirectory(Directory.systemTemp.path),
        Directory.systemTemp.path);
  });

  test('every launch uses an unguessable bearer token', () {
    final first = InstalledCoordinator.newToken();
    final second = InstalledCoordinator.newToken();
    expect(first.length, greaterThanOrEqualTo(32));
    expect(first, isNot(second));
    expect(first.contains('='), isFalse);
  });

  test('a start failure is reported without the token', () async {
    final coordinator = InstalledCoordinator(
        python: 'C:/missing/python.exe',
        root: 'C:/App',
        workspace: '',
        portPicker: () async => 8765,
        launcher: (python, arguments, cwd, environment) async =>
            throw const ProcessException('launch', []),
        probe: (base, token) async => false);
    await expectLater(
        coordinator.start(),
        throwsA(isA<CoordinatorStartFailure>().having(
            (failure) => failure.message,
            'message',
            contains('could not be started'))));
  });

  test('installed coordinator starts Python in isolated mode', () async {
    List<String>? observed;
    final coordinator = InstalledCoordinator(
        python: 'python',
        root: 'app',
        workspace: '',
        portPicker: () async => 8765,
        launcher: (python, arguments, cwd, environment) async {
          observed = arguments;
          throw const ProcessException('launch', []);
        });
    await expectLater(
        coordinator.start(), throwsA(isA<CoordinatorStartFailure>()));
    expect(observed!.take(3).toList(), ['-I', '-m', 'odessy.setup.installed']);
  });

  test('source coordinator keeps its explicitly supplied Python path',
      () async {
    List<String>? observed;
    Map<String, String>? environment;
    final coordinator = InstalledCoordinator(
        python: 'python',
        root: 'source',
        workspace: '',
        configuration: const {
          'config': 'config.json',
          'format': 'selection',
          'selection': <String, dynamic>{},
          'sha256': 'hash',
          'pythonpath': 'source-path',
        },
        portPicker: () async => 8765,
        launcher: (python, arguments, cwd, env) async {
          observed = arguments;
          environment = env;
          throw const ProcessException('launch', []);
        });
    await expectLater(
        coordinator.start(), throwsA(isA<CoordinatorStartFailure>()));
    expect(observed!.first, '-m');
    expect(environment!['PYTHONPATH'], 'source-path');
  });
}
