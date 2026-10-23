import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';

void main() {
  final python = Platform.environment['ODESSY_TEST_PYTHON'];
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
