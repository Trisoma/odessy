import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test(
      'LocalApi inspector shares authenticated bounded transport and refuses redirects',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var requests = 0, redirect = false;
    server.listen((r) async {
      requests++;
      expect(r.method, 'GET');
      expect(
          r.headers.value(HttpHeaders.authorizationHeader), 'Bearer fixture');
      if (redirect) {
        r.response.statusCode = 302;
        r.response.headers.set('location', '/elsewhere');
      } else {
        r.response.write(jsonEncode({
          'service_instance_id': 'instance',
          'session_id': 'sid',
          'workspace_id': 'workspace',
          'label': 'Local',
          'capabilities': {
            'read_files': true,
            'proposed_changes': false,
            'recorded_output': true
          }
        }));
      }
      await r.response.close();
    });
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    try {
      expect((await api.workspace('sid')).sessionId, 'sid');
      redirect = true;
      await expectLater(api.workspace('sid'), throwsA(isA<ApiFailure>()));
      expect(requests, 2);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
