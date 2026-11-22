import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test('SSH connection metadata is explicit and independent of pairing trust',
      () {
    final value = <String, dynamic>{
      'host_id': 'host_ssh',
      'display_name': 'ubuntu-fixture',
      'trust': {'state': 'unknown'},
      'connectivity': {'state': 'online'},
      'monitoring_access': 'allowed',
      'connection': {'method': 'ssh', 'alias': 'user@192.0.2.10'}
    };
    final host = MonitoringHost.fromJson(value);
    expect(host.name, 'ubuntu-fixture');
    expect(host.connectionMethod, 'ssh');
    expect(host.connectionAlias, 'user@192.0.2.10');
    expect(host.trust, 'unknown');
    expect(
        MonitoringHost.fromJson({...value, 'connection': null})
            .connectionMethod,
        'unknown');
    expect(() => MonitoringHost.fromJson({...value, 'connection': 'ssh'}),
        throwsFormatException);
    expect(
        () => MonitoringHost.fromJson({
              ...value,
              'connection': {'method': 'shell'}
            }),
        throwsFormatException);
    expect(
        () => MonitoringHost.fromJson({
              ...value,
              'connection': {'method': 'ssh', 'alias': 1}
            }),
        throwsFormatException);
  });
  test(
      'monitoring model distinguishes trust connectivity and unsupported metrics',
      () {
    final host = MonitoringHost.fromJson({
      'host_id': 'host_a',
      'display_name': 'Jetson',
      'trust': {'state': 'paired'},
      'connectivity': {'state': 'offline'},
      'monitoring_access': 'allowed',
      'sample': {
        'gpu': {'status': 'unsupported', 'devices': []}
      }
    });
    expect(host.trust, 'paired');
    expect(host.connectivity, 'offline');
    expect(host.sample?['gpu']['status'], 'unsupported');
    expect(() => MonitoringHost.fromJson({'host_id': true}),
        throwsFormatException);
  });

  test(
      'real HTTP sends auth, preserves opaque cursor, and never follows redirect',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final client = MonitoringClient(
        Uri.parse('http://127.0.0.1:${server.port}'), 'test-secret');
    final task = server.listen((request) async {
      expect(request.headers.value(HttpHeaders.authorizationHeader),
          'Bearer test-secret');
      expect(request.uri.query.contains('secret'), false);
      if (request.uri.path.endsWith('/events')) {
        expect(request.headers.value('Last-Event-ID'), 'epoch:1');
        request.response.headers.contentType =
            ContentType('text', 'event-stream');
        request.response.write(
            'id: epoch:2\nevent: resync_required\ndata: {"schema_version":1}\n\n');
      } else {
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode({
          'schema_version': 1,
          'hosts': [],
          'event_cursor': 'epoch:1',
          'next_cursor': null
        }));
      }
      await request.response.close();
    });
    try {
      expect((await client.listHosts()).cursor, 'epoch:1');
      final event = await client.watchEvents(after: 'epoch:1').first;
      expect(event.requiresResync, true);
    } finally {
      client.close();
      await task.cancel();
      await server.close(force: true);
    }
  });
}
