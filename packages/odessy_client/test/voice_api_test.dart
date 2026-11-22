import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('voice preserves bounded service error instead of a model-start error',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    server.listen((request) async {
      await request.drain<void>();
      request.response.statusCode = 409;
      request.response
          .write(jsonEncode({'detail': 'Finish capture before playback'}));
      await request.response.close();
    });
    try {
      await expectLater(
          api.voiceCommand('s', 'test_output'),
          throwsA(isA<VoiceActionFailure>().having(
              (e) => e.message, 'message', 'Finish capture before playback')));
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test(
      'live voice application sends exact saved fingerprint and authentication',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    server.listen((request) async {
      expect(request.method, 'POST');
      expect(request.uri.path, '/v1/voice/configuration');
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(jsonDecode(await utf8.decoder.bind(request).join()),
          {'fingerprint': 'exact'});
      request.response.write(
          jsonEncode({'status': 'deferred', 'message': 'Wait until idle.'}));
      await request.response.close();
    });
    try {
      expect(
          (await api.applyVoiceConfiguration('exact'))['status'], 'deferred');
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('voice requests are authenticated, session-scoped and validate status',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final paths = <String>[];
    final actions = <String>[];
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      paths.add('${request.method} ${request.uri.path}');
      if (request.method == 'POST') {
        final body = jsonDecode(await utf8.decoder.bind(request).join()) as Map;
        actions.add(body['action'] as String);
      }
      request.response.write(jsonEncode({
        'supported': malformed ? 'invalid' : true,
        'state': 'recording',
        'capture_id': 'capture-1',
        'recorder_host': 'coordinator',
      }));
      await request.response.close();
    });
    try {
      await api.voiceCommand('session-1', 'start_capture');
      await api.voiceCommand('session-1', 'cancel_capture',
          captureId: 'capture-1');
      await api.voiceState('session-1');
      expect(paths, [
        'POST /v1/sessions/session-1/voice',
        'POST /v1/sessions/session-1/voice',
        'GET /v1/sessions/session-1/voice'
      ]);
      expect(actions, ['start_capture', 'cancel_capture']);
      malformed = true;
      await expectLater(api.voiceState('session-1'), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
