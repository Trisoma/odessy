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
      'saved DeepSeek reaches the owned service with private session credentials',
      () async {
    final root = Directory.current.parent.parent.absolute.path;
    final folder =
        await Directory.systemTemp.createTemp('odessy-source-model-');
    final backend = LocalSetupBackend(
        python: python!,
        sourceRoot: root,
        pythonPath: Platform.environment['ODESSY_TEST_PYTHONPATH'] ?? '');
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final requests = <Json>[];
    final headers = <String?>[];
    server.listen((request) async {
      headers.add(request.headers.value(HttpHeaders.authorizationHeader));
      requests.add(jsonDecode(await utf8.decoder.bind(request).join()) as Json);
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode({
        'choices': [
          {
            'message': {
              'role': 'assistant',
              'content': 'Laptop model connection verified.'
            },
            'finish_reason': 'stop'
          }
        ],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 4}
      }));
      await request.response.close();
    });
    final endpoint = 'http://127.0.0.1:${server.port}/v1';
    final selection = <String, dynamic>{
      'role': 'combined',
      'model': {
        'provider': 'deepseek',
        'name': 'deepseek-flash',
        'endpoint': endpoint
      }
    };
    final output = '${folder.path}/selection.json';
    InstalledCoordinator? coordinator;
    LocalApi? api;
    Future<SetupReply> request(String operation, {String confirm = ''}) =>
        backend.execute(SetupRequest(
            id: operation,
            operation: operation,
            selection: selection,
            output: output,
            confirmPlan: confirm));
    try {
      final review = await request('review');
      expect(review.ok, isTrue, reason: review.reason);
      expect(
          (await request('save', confirm: review.result['plan_id'] as String))
              .ok,
          isTrue);
      final credential = await backend.execute(SetupRequest(
          id: 'credential',
          operation: 'model-credential',
          selection: const {},
          parameters: {
            'action': 'set',
            'provider': 'deepseek',
            'endpoint': endpoint,
            'storage': 'session',
            'value': 'source-preview-dummy-key'
          }));
      expect(credential.ok, isTrue, reason: credential.reason);
      final launch = await request('launch');
      expect(launch.ok, isTrue, reason: launch.reason);
      expect(launch.result['selection']['model']['provider'], 'deepseek');
      coordinator = InstalledCoordinator(
          python: launch.result['python'] as String,
          root: '',
          workspace: '${folder.path}/workspace',
          configuration: launch.result,
          launcher: (interpreter, arguments, cwd, environment) =>
              Process.start(interpreter, arguments,
                  workingDirectory: cwd,
                  environment: {
                    ...backend.processEnvironment,
                    ...environment,
                    'ODESSY_CONFIG_FILE': '${folder.path}/odessy.env',
                    'ODESSY_COLLABORATION_FILE':
                        '${folder.path}/collaboration.toml',
                    'ODESSY_MONITORING': 'off',
                    'ODESSY_LINK_STATE_DIR': '${folder.path}/link',
                  },
                  runInShell: false));
      api = await coordinator.start();
      final capabilities = await api.runtimeCapabilities();
      expect(capabilities.configuration!.provider, 'deepseek');
      expect(capabilities.configuration!.modelName, 'deepseek-flash');
      expect(requests, isEmpty, reason: 'Startup must not perform inference.');
      final session = await api.createSession();
      final done = Completer<void>();
      final subscription = api.events(session.id, 0).listen((event) {
        if (event.type == 'result' && !done.isCompleted) done.complete();
        if (event.type == 'error' && !done.isCompleted) {
          done.completeError(StateError('Model turn failed: ${event.data}'));
        }
      });
      try {
        await api.send(session.id, 'Verify laptop model routing.');
        await done.future.timeout(const Duration(seconds: 20));
      } finally {
        await subscription.cancel();
      }
      expect(requests.single['model'], 'deepseek-flash');
      expect(headers.single, 'Bearer source-preview-dummy-key');
      expect(jsonEncode(requests), isNot(contains('source-preview-dummy-key')));
      expect(await File(output).readAsString(),
          isNot(contains('source-preview-dummy-key')));
      final snapshot = await api.snapshot(session.id);
      expect(
          snapshot.messages.any(
              (message) => message.text == 'Laptop model connection verified.'),
          isTrue);
      await api.deleteSession(session.id);
    } finally {
      await coordinator?.stop();
      api?.close();
      await sessionModelCredentials.clear();
      await backend.close();
      await server.close(force: true);
      await folder.delete(recursive: true);
    }
  },
      skip: python == null
          ? 'Set ODESSY_TEST_PYTHON for real source service qualification.'
          : false,
      timeout: const Timeout(Duration(minutes: 2)));
}
