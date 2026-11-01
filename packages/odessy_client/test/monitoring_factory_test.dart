import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test('owned factory uses identical origin/auth and repeated history series',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api = LocalApi(
        Uri.parse('http://127.0.0.1:${server.port}'), 'fixture-bearer');
    final monitor = api.createMonitoringClient();
    var requests = 0;
    server.listen((request) async {
      requests++;
      expect(request.headers.value('authorization'), 'Bearer fixture-bearer');
      expect(request.uri.query.contains('bearer'), false);
      if (request.uri.path.endsWith('/history')) {
        expect(request.uri.queryParametersAll['series'],
            ['cpu.utilization_percent', 'memory.used_bytes']);
        expect(request.uri.queryParameters['step'], '60');
      } else {
        expect(request.uri.path, '/v1/monitoring');
      }
      request.response
          .write(jsonEncode({'schema_version': 1, 'history_degrade': ''}));
      await request.response.close();
    });
    try {
      expect((await monitor.getOverview())['history_degrade'], '');
      await monitor.getHistory('host_a',
          from: DateTime.utc(2026),
          to: DateTime.utc(2026, 1, 1, 1),
          series: 'cpu.utilization_percent,memory.used_bytes');
      expect(requests, 2);
      monitor.close();
      final replacement = api.createMonitoringClient();
      await replacement.getOverview();
      api.close();
      expect(() => api.createMonitoringClient(), throwsStateError);
      await expectLater(replacement.getOverview(), throwsStateError);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  for (final closeOwner in [false, true]) {
    test('silent SSE closes without another event (owner close: $closeOwner)',
        () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final api =
          LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
      final monitor = api.createMonitoringClient();
      server.listen((request) async {
        request.response.bufferOutput = false;
        request.response.headers.contentType =
            ContentType('text', 'event-stream');
        request.response.write(
            'id: epoch:1\nevent: host.metrics\ndata: {"schema_version":1,"host_id":"host_a"}\n\n');
        await request.response.flush();
      });
      final seen = Completer<void>();
      final sub = monitor.watchEvents().listen((event) {
        if (!seen.isCompleted) seen.complete();
      }, onError: (Object _) {});
      try {
        await seen.future.timeout(const Duration(seconds: 3));
        if (closeOwner) api.close();
        await sub.cancel().timeout(const Duration(seconds: 1));
        api.close();
        await expectLater(monitor.getOverview(), throwsStateError);
      } finally {
        api.close();
        await server.close(force: true);
      }
    });
  }
  test('monitor refuses redirects and malformed/duplicate history selections',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final monitor = MonitoringClient(
        Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var requests = 0;
    server.listen((request) async {
      requests++;
      request.response.statusCode = 302;
      request.response.headers.set('location', '/unexpected');
      await request.response.close();
    });
    try {
      await expectLater(
          monitor.getOverview(), throwsA(isA<MonitoringFailure>()));
      expect(requests, 1);
      for (final names in [
        '',
        'cpu.utilization_percent,',
        'cpu.utilization_percent,cpu.utilization_percent',
        '../secret'
      ]) {
        expect(
            () => monitor.getHistory('host_a',
                from: DateTime.utc(2026),
                to: DateTime.utc(2026, 1, 1, 1),
                series: names),
            throwsFormatException);
      }
    } finally {
      monitor.close();
      await server.close(force: true);
    }
  });
  test('monitoring rejects malformed response schemas over real HTTP',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    server.listen((request) async {
      request.response.write('{"schema_version":2}');
      await request.response.close();
    });
    try {
      await expectLater(
          api.createMonitoringClient().getOverview(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
