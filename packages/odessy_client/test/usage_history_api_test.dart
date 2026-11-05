import 'dart:convert';
import 'dart:io';

import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('history uses loopback bearer and encodes filters', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api = LocalApi(
      Uri.parse('http://127.0.0.1:${server.port}'),
      'secret',
    );
    final request = server.first.then((req) async {
      expect(req.method, 'GET');
      expect(req.headers.value('authorization'), 'Bearer secret');
      expect(req.uri.path, '/v1/usage/history');
      expect(req.uri.queryParameters['provider'], 'provider & name');
      expect(req.uri.queryParameters['model'], 'model/a');
      expect(req.uri.queryParameters['days'], '7');
      req.response.headers.contentType = ContentType.json;
      req.response.write(jsonEncode({'schema_version': 1}));
      await req.response.close();
    });
    try {
      final result = await api.usageHistory(
        days: 7,
        provider: 'provider & name',
        model: 'model/a',
      );
      expect(result['schema_version'], 1);
      await request;
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
