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
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      paths.add('${request.method} ${request.uri.path}');
      request.response.write(jsonEncode({
        'state': malformed ? 'unknown' : 'starting',
        'input_available': false,
        'output_available': false
      }));
      await request.response.close();
    });
    try {
      await api.startVoice('session-1');
      await api.heartbeatVoice('session-1');
      await api.stopVoice('session-1');
      expect(paths, [
        'POST /v1/sessions/session-1/voice/start',
        'POST /v1/sessions/session-1/voice/heartbeat',
        'POST /v1/sessions/session-1/voice/stop'
      ]);
      malformed = true;
      await expectLater(api.voiceStatus('session-1'), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
