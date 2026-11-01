import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';

class _RealHttp extends HttpOverrides {}

void main() {
  testWidgets(
      'real loopback monitoring composes into app without demo fallback or mutations',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await HttpOverrides.runZoned(() async {
      final server = (await tester
          .runAsync(() => HttpServer.bind(InternetAddress.loopbackIPv4, 0)))!;
      final api = LocalApi(
          Uri.parse('http://127.0.0.1:${server.port}'), 'fixture-auth');
      final calls = <String>[];
      final queries = <Uri>[];
      var monitoringDown = false;
      final host = {
        'host_id': 'host_fixture',
        'display_name': 'Server A',
        'trust': {'state': 'paired'},
        'connectivity': {'state': 'online'},
        'monitoring_access': 'allowed'
      };
      final session = {
        'id': 'fixture-session',
        'selected_profile': 'auto',
        'running': false,
        'history': [],
        'events': [],
        'pending_approvals': [],
        'pending_questions': []
      };
      server.listen((request) async {
        expect(request.headers.value('authorization'), 'Bearer fixture-auth');
        final path = request.uri.path;
        if (monitoringDown && path.startsWith('/v1/monitoring')) {
          request.response.statusCode = 503;
          request.response.write(
              '{"schema_version":1,"error":{"code":"MONITOR_UNAVAILABLE"}}');
          await request.response.close();
          return;
        }
        calls.add('${request.method} $path');
        if (path.endsWith('/events')) {
          request.response.bufferOutput = false;
          request.response.headers.contentType =
              ContentType('text', 'event-stream');
          request.response.write(': fixture\n\n');
          await request.response.flush();
          return;
        }
        Object response;
        if (path == '/v1/profiles') {
          response = {
            'profiles': {'auto': {}}
          };
        } else if (path.startsWith('/v1/sessions')) {
          response = session;
        } else if (path == '/v1/monitoring') {
          response = {'schema_version': 1, 'history_degrade': ''};
        } else if (path == '/v1/monitoring/hosts') {
          response = {
            'schema_version': 1,
            'hosts': [host],
            'event_cursor': 'fixture:0',
            'next_cursor': null
          };
        } else if (path.endsWith('/metrics')) {
          response = {
            'schema_version': 1,
            'host_id': 'host_fixture',
            'sample': {
              'observed_at': '2026-10-26T12:00:00Z',
              'cpu': {'status': 'ok', 'utilization_percent': 33.3},
              'memory': {'status': 'unsupported'},
              'gpu': {'status': 'unsupported', 'devices': []}
            },
            'stale_groups': [],
            'services': []
          };
        } else if (path.endsWith('/history')) {
          queries.add(request.uri);
          expect(request.uri.queryParametersAll['series']!.length, 6);
          response = {
            'schema_version': 1,
            'host_id': 'host_fixture',
            'from': request.uri.queryParameters['from'],
            'to': request.uri.queryParameters['to'],
            'step_seconds': int.parse(request.uri.queryParameters['step']!),
            'series': [
              {'name': 'cpu.utilization_percent', 'points': null}
            ]
          };
        } else if (path == '/v1/monitoring/hosts/host_fixture') {
          response = {'schema_version': 1, 'host': host};
        } else {
          request.response.statusCode = 404;
          response = {'error': 'fixture route unavailable'};
        }
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode(response));
        await request.response.close();
      });
      Future<void> ready(bool Function() condition) async {
        for (var i = 0; i < 100; i++) {
          await tester.runAsync(
              () => Future<void>.delayed(const Duration(milliseconds: 10)));
          await tester.pump();
          if (condition()) return;
        }
        fail('App monitoring did not settle');
      }

      try {
        await tester.pumpWidget(OdessyApp(api: api));
        await ready(() => find.text('Server A').evaluate().isNotEmpty);
        expect(find.text('DEMO'), findsNothing);
        expect(find.text('Jetson'), findsNothing);
        await tester.tap(find.text('Server A'));
        await tester.pump();
        await ready(() => find.text('33.3%').evaluate().isNotEmpty);
        expect(find.text('host_fixture'), findsOneWidget);
        await tester.tap(find.widgetWithText(OutlinedButton, 'History'));
        await tester.pump();
        await ready(() =>
            find.text('Monitoring history').evaluate().isNotEmpty &&
            !find.text('Loading history').evaluate().isNotEmpty);
        expect(find.text('No returned buckets'), findsOneWidget);
        await tester.tap(find.byKey(const Key('monitoring-history-window')));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Last 7 days').last);
        await tester.pump();
        await ready(() =>
            queries.isNotEmpty &&
            queries.last.queryParameters['step'] == '900');
        await ready(
            () => find.text('900 seconds per bucket').evaluate().isNotEmpty);
        expect(
            calls.where(
                (c) => c.contains('/v1/monitoring') && !c.startsWith('GET ')),
            isEmpty);
        monitoringDown = true;
        await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
        await tester.pump();
        await tester.tap(find.byTooltip('Refresh devices'));
        await ready(
            () => find.textContaining('HTTP 503').evaluate().isNotEmpty);
        expect(find.text('Ready'), findsOneWidget);
        expect(find.text('DEMO'), findsNothing);
        expect(find.text('Jetson'), findsNothing);
        api.close();
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 20)));
        await tester.pumpAndSettle();
      } finally {
        api.close();
        await tester.runAsync(() => server.close(force: true));
      }
    }, createHttpClient: (context) => _RealHttp().createHttpClient(context));
  });
}
