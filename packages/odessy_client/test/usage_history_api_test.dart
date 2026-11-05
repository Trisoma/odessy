import 'dart:convert';
import 'dart:io';

import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

Json validHistory() => {
      'schema_version': 1,
      'summary': {
        'requests': 0,
        'prompt': null,
        'cached': null,
        'completion': null,
        'cost': null,
        'currency': null
      },
      'daily': <Json>[],
      'groups': <Json>[],
      'providers': <Json>[],
      'requests': <Json>[],
      'options': <Json>[],
      'total': 0,
      'limit': 50,
      'offset': 0,
    };

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
      req.response.write(jsonEncode(validHistory()));
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

  test('rejects malformed nested history and unsupported query arguments',
      () async {
    expect(
        () => validateUsageHistory({
              ...validHistory(),
              'summary': {'requests': 'one'}
            }),
        throwsFormatException);
    expect(
        () => validateUsageHistory({
              ...validHistory(),
              'daily': [
                {'day': 'bad', 'requests': 1}
              ]
            }),
        throwsFormatException);
    expect(
        () => validateUsageHistory({
              ...validHistory(),
              'requests': ['bad']
            }),
        throwsFormatException);
    final api = LocalApi(Uri.parse('http://127.0.0.1:1'), 'secret');
    try {
      await expectLater(api.usageHistory(days: 31), throwsArgumentError);
      await expectLater(api.usageHistory(offset: 100001), throwsArgumentError);
    } finally {
      api.close();
    }
  });

  test('rejects malformed history from the HTTP service', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'secret');
    final handled = server.first.then((req) async {
      req.response.headers.contentType = ContentType.json;
      req.response.write(jsonEncode({
        ...validHistory(),
        'summary': {'requests': 'invalid'}
      }));
      await req.response.close();
    });
    try {
      await expectLater(api.usageHistory(), throwsFormatException);
      await handled;
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
