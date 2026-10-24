import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';

void main() {
  final python = Platform.environment['ODESSY_TEST_PYTHON'];
  test(
      'real maintenance bridge detects legacy, uninstalls and reopens without replay',
      () async {
    final repo = Directory.current.parent.parent.absolute.path;
    final directory =
        Directory.systemTemp.createTempSync('odessy-maintenance-bridge-');
    final installation = '${directory.path}${Platform.pathSeparator}owned';
    const fixture = r'''import sys
from pathlib import Path
from installer.core.checkpoint import Step,run_steps
root=Path(sys.argv[1])
run_steps(root,{'install_dir':str(root),'engine':'role-aware-existing-installer-v1','selection':{'role':'execution'}},[Step('application',lambda:{},lambda:{})])
app=root/'application';app.mkdir();(app/'odessy-application.json').write_text('{"schema_version":1}');(app/'owned.txt').write_text('owned')
(root/'models').mkdir();(root/'models/external').write_text('preserve')
(root/'note.txt').write_text('preserve')
''';
    final created = await Process.run(python!, ['-c', fixture, installation],
        workingDirectory: repo,
        environment: {
          'PYTHONPATH':
              '$repo${Platform.pathSeparator}src${Platform.isWindows ? ';' : ':'}$repo'
        });
    expect(created.exitCode, 0, reason: '${created.stderr}');
    final backend = LocalSetupBackend(python: python, sourceRoot: repo);
    var id = 0;
    Future<SetupReply> request(String action, {String? confirm}) =>
        backend.execute(SetupRequest(
            id: 'maintenance-${++id}',
            operation: 'manage',
            selection: const {},
            parameters: {
              'action': action,
              'root': installation,
              if (confirm != null) 'confirm': confirm
            }));
    try {
      expect(
          (await request('inspect')).result['installation_format'], 'legacy');
      final plan = await request('uninstall-plan');
      expect(plan.ok, true);
      final done =
          await request('uninstall', confirm: plan.result['plan_id'] as String);
      expect(done.ok, true);
      expect(done.result['status'], 'completed');
      expect(
          File('$installation/models/external').readAsStringSync(), 'preserve');
      expect(File('$installation/note.txt').readAsStringSync(), 'preserve');
      await backend.close();
      final reopened = LocalSetupBackend(python: python, sourceRoot: repo);
      try {
        final state = await reopened.execute(SetupRequest(
            id: 'reopened',
            operation: 'manage',
            selection: const {},
            parameters: {'action': 'inspect', 'root': installation}));
        expect(state.result['uninstall']['status'], 'completed');
      } finally {
        await reopened.close();
      }
    } finally {
      await backend.close();
      directory.deleteSync(recursive: true);
    }
  },
      skip: python == null
          ? 'Set ODESSY_TEST_PYTHON for real lifecycle integration'
          : false,
      timeout: const Timeout(Duration(seconds: 90)));

  test('real Python bridge reviews, saves once and recovers without replay',
      () async {
    final root = Directory.current.parent.parent.absolute.path;
    final directory =
        Directory.systemTemp.createTempSync('odessy-wizard-test-');
    final backend = LocalSetupBackend(python: python!, sourceRoot: root);
    final output = '${directory.path}${Platform.pathSeparator}selection.json';
    final selection = <String, dynamic>{'role': 'combined'};
    try {
      final review = await backend.execute(SetupRequest(
          id: 'one',
          operation: 'review',
          selection: selection,
          output: output));
      expect(review.ok, isTrue);
      expect(review.result['downloads'], isEmpty);
      final saved = await backend.execute(SetupRequest(
          id: 'two',
          operation: 'save',
          selection: selection,
          output: output,
          confirmPlan: review.result['plan_id'] as String));
      expect(saved.result['saved'], isTrue);
      expect(File(output).existsSync(), isTrue);
      final recovered = await backend.execute(SetupRequest(
          id: 'three',
          operation: 'recover',
          selection: selection,
          output: output));
      expect(recovered.result['recovery'], 'matches');
      final again = await backend.execute(SetupRequest(
          id: 'four',
          operation: 'save',
          selection: selection,
          output: output,
          confirmPlan: review.result['plan_id'] as String));
      expect(again.ok, isFalse);
    } finally {
      await backend.close();
      directory.deleteSync(recursive: true);
    }
  },
      skip: python == null
          ? 'Set ODESSY_TEST_PYTHON for real local bridge qualification.'
          : false);

  test('cancelling slow discovery stops the owned helper and permits retry',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final observed = server.listen((request) {});
    final backend = LocalSetupBackend(
        python: python!,
        sourceRoot: Directory.current.parent.parent.absolute.path);
    try {
      final operation = backend.execute(SetupRequest(
          id: 'slow',
          operation: 'discover',
          selection: {'role': 'combined'},
          endpoint: 'http://127.0.0.1:${server.port}'));
      final expected = expectLater(operation, throwsA(isA<SetupCancelled>()));
      await Future<void>.delayed(const Duration(milliseconds: 400));
      await backend.cancel();
      await expected;
      expect(
          (await backend.execute(SetupRequest(
                  id: 'retry',
                  operation: 'review',
                  selection: {'role': 'execution'})))
              .ok,
          isTrue);
    } finally {
      await backend.close();
      await observed.cancel();
      await server.close(force: true);
    }
  },
      skip: python == null
          ? 'Set ODESSY_TEST_PYTHON for real local cancellation qualification.'
          : false);
}
