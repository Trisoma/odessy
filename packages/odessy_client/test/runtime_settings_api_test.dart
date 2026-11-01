import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('runtime inventory uses bearer and rejects value-bearing replies',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var malformed = false;
    server.listen((request) async {
      expect(request.uri.path, '/v1/runtime-settings');
      expect(request.headers.value('authorization'), 'Bearer fixture');
      request.response.write(jsonEncode({
        'schema_version': 1,
        'instance_id': 'instance',
        'settings': [
          {
            'name': 'ODESSY_TEST',
            'kind': 'secret',
            'summary': 'Test',
            'configured': true,
            'sensitive': true,
            'internal': false,
            'editable': false,
            if (malformed) 'value': 'do-not-display'
          }
        ]
      }));
      await request.response.close();
    });
    try {
      expect((await api.runtimeSettings())['instance_id'], 'instance');
      malformed = true;
      await expectLater(api.runtimeSettings(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
