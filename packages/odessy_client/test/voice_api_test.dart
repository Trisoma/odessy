import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
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
