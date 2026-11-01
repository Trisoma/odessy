import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test(
      'submission and cancellation use session routes without an approval flag',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final id = 'b' * 32;
    final received = <(String, String, String)>[];
    server.listen((request) async {
      final body = await utf8.decoder.bind(request).join();
      received.add((request.method, request.uri.path, body));
      request.response.write(jsonEncode({'job_id': id, 'state': 'queued'}));
      await request.response.close();
    });
    try {
      await api.submitJob('session-1',
          {'participant': 'coder', 'task': 'review', 'mode': 'advice'});
      await api.cancelJob('session-1', id);
      expect(received[0].$1, 'POST');
      expect(received[0].$2, '/v1/sessions/session-1/jobs');
      expect(jsonDecode(received[0].$3),
          {'participant': 'coder', 'task': 'review', 'mode': 'advice'});
      expect(received[1].$2, '/v1/sessions/session-1/jobs/$id/cancel');
      expect(received[1].$3, isEmpty);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });

  test('job list uses session scope, bearer and query; rejects malformed pages',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final id = 'a' * 32;
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/sessions/session-1/jobs');
      expect(request.uri.queryParameters['limit'], '2');
      expect(request.uri.queryParameters['after'], id);
      request.response.write(jsonEncode({
        'jobs': [
          {
            'job_id': id,
            'state': 'running',
            'participant': 'p',
            'mode': 'advice',
            'remote_state_unknown': malformed ? 'no' : false
          }
        ],
        'next_cursor': null
      }));
      await request.response.close();
    });
    try {
      final result = await api.listJobs('session-1', after: id, limit: 2);
      expect((result['jobs'] as List).length, 1);
      malformed = true;
      await expectLater(api.listJobs('session-1', after: id, limit: 2),
          throwsFormatException);
      expect(() => api.readJob('session-1', '../x'), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
