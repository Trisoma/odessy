import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  final page = <String, dynamic>{
    'service_instance_id': 'fixture-instance',
    'sessions': [
      {
        'id': 'fixture',
        'engine': 'harness',
        'selected_profile': 'local',
        'running': false
      }
    ],
    'next_offset': null
  };

  test('typed catalog rejects malformed and duplicate session summaries', () {
    expect(
        SessionDirectoryPage.fromJson(page).sessions.single.engine, 'harness');
    for (final invalid in [
      {...page, 'service_instance_id': ''},
      {
        ...page,
        'sessions': [1]
      },
      {
        ...page,
        'sessions': [...page['sessions'] as List, ...page['sessions'] as List]
      },
      {...page, 'next_offset': -1},
      {
        ...page,
        'sessions': [
          {
            'id': 's',
            'engine': 'unknown',
            'selected_profile': 'p',
            'running': false
          }
        ]
      }
    ]) {
      expect(
          () => SessionDirectoryPage.fromJson(invalid), throwsFormatException);
    }
  });

  test(
      'local transport authenticates directory reads without mutations or retries',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final requests = <String>[];
    var status = 200;
    server.listen((request) async {
      requests.add(request.method);
      expect(request.headers.value(HttpHeaders.authorizationHeader),
          'Bearer fixture-secret');
      expect(request.uri.path, '/v1/sessions');
      expect(request.uri.queryParameters, {'limit': '2', 'offset': '0'});
      expect(request.uri.query, isNot(contains('fixture-secret')));
      request.response.statusCode = status;
      request.response.write(jsonEncode(page));
      await request.response.close();
    });
    final api = LocalApi(
        Uri.parse('http://127.0.0.1:${server.port}'), 'fixture-secret');
    try {
      expect((await api.listSessions(limit: 2)).sessions.single.id, 'fixture');
      status = 403;
      await expectLater(api.listSessions(limit: 2), throwsA(isA<ApiFailure>()));
      expect(requests, ['GET', 'GET']);
      await expectLater(api.listSessions(limit: 101), throwsArgumentError);
      expect(requests.length, 2);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
