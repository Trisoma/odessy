import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';
import 'package:odessy_desktop/app/setup/session_model_credentials.dart';

void main() {
  final python = Platform.environment['ODESSY_TEST_PYTHON'];
  test(
      'real Dart bridge, native vault, reopened helper and coordinator use rotated keys without environment injection',
      () async {
    final repo = Directory.current.parent.parent.absolute.path;
    final folder =
        await Directory.systemTemp.createTemp('odessy-credential-integration-');
    final backend = LocalSetupBackend(python: python!, sourceRoot: repo);
    final headers = <String?>[];
    final bodies = <String>[];
    final cloud = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final endpoint = 'http://127.0.0.1:${cloud.port}/v1';
    cloud.listen((request) async {
      headers.add(request.headers.value(HttpHeaders.authorizationHeader));
      bodies.add(await utf8.decoder.bind(request).join());
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode({
        'choices': [
          {
            'message': {
              'role': 'assistant',
              'content': 'Local credential qualification succeeded.'
            },
            'finish_reason': 'stop'
          }
        ],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 4}
      }));
      await request.response.close();
    });
    final selection = <String, dynamic>{
      'role': 'combined',
      'model': {
        'provider': 'custom',
        'name': 'qualification-model',
        'endpoint': endpoint
      }
    };
    final scope = <String, dynamic>{'provider': 'custom', 'endpoint': endpoint};
    Future<SetupReply> credential(String action,
            {String? value, String storage = 'keyring'}) =>
        backend.execute(SetupRequest(
            id: 'integration-${DateTime.now().microsecondsSinceEpoch}',
            operation: 'model-credential',
            selection: const {},
            parameters: {
              ...scope,
              'action': action,
              if (action != 'describe') 'storage': storage,
              if (value != null) 'value': value
            }));
    InstalledCoordinator? coordinator;
    LocalApi? api;
    try {
      final saved = await credential('set', value: 'persistent-fixture-key');
      expect(saved.ok, isTrue, reason: saved.reason);
      expect(saved.result['source'], 'keyring');
      final reopened = LocalSetupBackend(python: python, sourceRoot: repo);
      try {
        final status = await reopened.execute(SetupRequest(
            id: 'reopened',
            operation: 'model-credential',
            selection: const {},
            parameters: {...scope, 'action': 'describe'}));
        expect(status.result['source'], 'keyring');
        expect(jsonEncode(status.result),
            isNot(contains('persistent-fixture-key')));
      } finally {
        await reopened.close();
      }
      final output = '${folder.path}/selection.json';
      final review = await backend.execute(SetupRequest(
          id: 'review',
          operation: 'review',
          selection: selection,
          output: output));
      final savedConfig = await backend.execute(SetupRequest(
          id: 'save',
          operation: 'save',
          selection: selection,
          output: output,
          confirmPlan: review.result['plan_id'] as String));
      expect(savedConfig.ok, isTrue, reason: savedConfig.reason);
      final launch = await backend.execute(SetupRequest(
          id: 'launch',
          operation: 'launch',
          selection: selection,
          output: output));
      expect(launch.ok, isTrue, reason: launch.reason);
      coordinator = InstalledCoordinator(
          python: python,
          root: '',
          workspace: '${folder.path}/workspace',
          configuration: launch.result);
      api = await coordinator.start();
      final session = await api.createSession();
      Future<void> turn() async {
        final client = api!;
        final result = Completer<void>();
        final previous = (await client.snapshot(session.id)).events;
        final subscription = client
            .events(session.id, previous.isEmpty ? 0 : previous.last.id)
            .listen((event) {
          if (event.type == 'result' && !result.isCompleted) result.complete();
          if (event.type == 'error' && !result.isCompleted) {
            result.completeError(StateError('Fixture turn failed.'));
          }
        });
        try {
          await client.send(session.id, 'Say hello.');
          await result.future.timeout(const Duration(seconds: 20));
        } finally {
          await subscription.cancel();
        }
      }

      await turn();
      expect(headers.last, 'Bearer persistent-fixture-key');
      final ephemeral = await credential('set',
          value: 'session-fixture-key', storage: 'session');
      expect(ephemeral.result['source'], 'session');
      await turn();
      expect(headers.last, 'Bearer session-fixture-key');
      await sessionModelCredentials.clear();
      await turn();
      expect(headers.last, 'Bearer persistent-fixture-key');
      expect(bodies.every((body) => !body.contains('fixture-key')), isTrue);
      expect(await File(output).readAsString(), isNot(contains('fixture-key')));
      await api.deleteSession(session.id);
    } finally {
      await coordinator?.stop();
      api?.close();
      await sessionModelCredentials.clear();
      await credential('remove');
      await backend.close();
      await cloud.close(force: true);
      await folder.delete(recursive: true);
    }
  },
      skip: python == null ||
              Platform.environment['ODESSY_TEST_NATIVE_KEYRING'] != '1'
          ? 'Requires explicit native Windows/Linux credential qualification with dummy keys.'
          : false,
      timeout: const Timeout(Duration(minutes: 2)));
}
