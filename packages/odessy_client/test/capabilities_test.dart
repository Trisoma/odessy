import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

Json fixture() => {
      'schema_version': 1,
      'service_instance_id': 'a' * 32,
      'restart_safe': true,
      'configuration': {
        'owner': 'b' * 64,
        'fingerprint': 'c' * 64,
        'fingerprints': {
          for (final key in ['llm', 'stt', 'tts', 'voice', 'host'])
            key: 'd' * 64
        },
        'model': {'provider': 'ollama', 'name': 'A'},
        'workspace': '/work',
        'role': 'combined'
      }
    };

void main() {
  test('descriptors reject malformed revisions and missing section hashes', () {
    final bad = fixture();
    (bad['configuration'] as Json)['owner'] = 'not-an-owner';
    expect(() => RuntimeCapabilities.fromJson(bad), throwsFormatException);
    final missing = fixture();
    ((missing['configuration'] as Json)['fingerprints'] as Map).remove('tts');
    expect(() => RuntimeCapabilities.fromJson(missing), throwsFormatException);
  });
  test(
      'authenticated capability/restart transport uses explicit mutations once',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final paths = <String>[];
    final observed = server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer private-token');
      paths.add(request.uri.path);
      final body = await utf8.decoder.bind(request).join();
      final response = request.response..headers.contentType = ContentType.json;
      if (request.uri.path == '/v1/capabilities')
        response.write(jsonEncode(fixture()));
      else if (request.uri.path.endsWith('prepare-restart')) {
        expect(jsonDecode(body), {'service_instance_id': 'a' * 32});
        response.write('{"ticket":"one-use-reservation"}');
      } else {
        expect(jsonDecode(body), {'ticket': 'one-use-reservation'});
        response.write('{"status":"resumed"}');
      }
      await response.close();
    });
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'private-token');
    try {
      final state = await api.runtimeCapabilities();
      expect(state.configuration!.modelName, 'A');
      final ticket = await api.prepareRestart(state.serviceInstance);
      await api.resumeAdmission(ticket);
      expect(paths, [
        '/v1/capabilities',
        '/v1/lifecycle/prepare-restart',
        '/v1/lifecycle/resume'
      ]);
    } finally {
      api.close();
      await observed.cancel();
      await server.close(force: true);
    }
  });
}
