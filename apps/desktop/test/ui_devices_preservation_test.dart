import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/devices/controllers/device_monitoring_controller.dart';
import 'package:odessy_desktop/features/devices/presentation/devices.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_details.dart';
import 'package:odessy_desktop/features/monitoring/presentation/metric_presentation.dart';

import 'device_monitoring_controller_test.dart' show FakeMonitoring, until;
import 'monitoring_presentation_test.dart'
    show monitoringHostFixture, mountMonitoring;

DeviceMonitoringController controllerFor(FakeMonitoring fake) {
  final controller = DeviceMonitoringController(
      clock: () => DateTime.utc(2026, 10, 25, 6, 5),
      createClient: (_) => fake,
      eventDelay: Duration.zero,
      retryDelay: const Duration(hours: 1));
  addTearDown(controller.dispose);
  return controller;
}

LocalApi api([int port = 1]) {
  final result = LocalApi(Uri.parse('http://127.0.0.1:$port'), 'fixture');
  addTearDown(result.close);
  return result;
}

/// Report with one group the collector refused and no first/second GPU value.
Json honestReport(String id) => {
      'schema_version': 1,
      'host_id': id,
      'stale_groups': <String>['gpu'],
      'sample': {
        'observed_at': '2026-10-25T06:04:30Z',
        'cpu': {'status': 'ok', 'utilization_percent': 0},
        'memory': {
          'status': 'ok',
          'used_bytes': 4294967296,
          'total_bytes': 17179869184
        },
        'gpu': {'status': 'permission_denied'},
      },
      'services': <Map<String, Object?>>[],
    };

void main() {
  test('first status is loading, then a live reading with a real source',
      () async {
    final fake = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_a', trust: 'local')]
      ..metricReply = (id) async => honestReport(id);
    final controller = controllerFor(fake)..bind(api());
    expect(controller.loadingInventory, isTrue);
    expect(controller.readingsLive, isFalse);
    await until(
        () => !controller.loadingInventory && !controller.loadingMetrics);

    expect(controller.metrics?['host_id'], 'host_a');
    expect(controller.readingsLive, isTrue);
    expect(controller.readingsRestricted, isFalse);
    expect(controller.sampleObservedAt, DateTime.utc(2026, 10, 25, 6, 4, 30));
    // Clock is 06:05, so the sample is 30s old; the age is measured, not fixed.
    expect(controller.sampleAge, const Duration(seconds: 30));
    expect(controller.staleGroups, contains('gpu'));
  });

  test('an unreported observation time stays unknown, never zero', () async {
    final fake = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_a', trust: 'local')]
      ..metricReply = (id) async => {
            'schema_version': 1,
            'host_id': id,
            'stale_groups': <String>[],
            'sample': {
              'cpu': {'status': 'ok', 'utilization_percent': 0}
            },
            'services': <Map<String, Object?>>[],
          };
    final controller = controllerFor(fake)..bind(api());
    await until(
        () => !controller.loadingInventory && !controller.loadingMetrics);
    expect(controller.sampleObservedAt, isNull);
    expect(controller.sampleAge, isNull);
    expect(freshnessLabel(controller.sampleAge),
        'Unknown · no observation time reported');
    // A genuine zero utilization is still zero, not "unknown".
    expect((controller.metrics?['sample'] as Map)['cpu']['utilization_percent'],
        0);
  });

  test('a late reply for the previous target is ignored', () async {
    final fake = FakeMonitoring()
      ..hosts = [
        monitoringHostFixture(id: 'host_a', trust: 'local'),
        monitoringHostFixture(id: 'host_b', trust: 'local'),
      ];
    final controller = controllerFor(fake)..bind(api());
    await until(() => controller.metrics?['host_id'] == 'host_a');

    // A refresh for host_a is still in flight when the user selects host_b.
    final slow = Completer<Json>();
    final started = Completer<void>();
    fake.metricReply = (id) {
      if (id != 'host_a') return Future.value(honestReport(id));
      started.complete();
      return slow.future;
    };
    final refresh = controller.refreshSelected();
    await started.future;
    controller.select('host_b');
    await until(() => controller.metrics?['host_id'] == 'host_b');

    slow.complete(honestReport('host_a'));
    await refresh;
    expect(controller.selectedId, 'host_b');
    expect(controller.metrics?['host_id'], 'host_b');
  });

  test('denied telemetry clears readings and is not presented as live',
      () async {
    final fake = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_a', trust: 'local')];
    final controller = controllerFor(fake)..bind(api());
    await until(() => controller.metrics != null);
    expect(controller.readingsLive, isTrue);

    fake.hosts = [
      monitoringHostFixture(id: 'host_a', trust: 'local', access: 'denied')
    ];
    await controller.refresh(manual: false);
    await until(() => controller.inspectedHost?.access == 'denied');
    expect(controller.readingsLive, isFalse);
    expect(controller.readingsRestricted, isTrue);
    expect(controller.metrics, isNull);
    expect(controller.staleGroups, containsAll(['cpu', 'memory', 'gpu']));
  });

  test('revocation drops retained values instead of showing them as current',
      () async {
    final fake = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_a', trust: 'paired')];
    final controller = controllerFor(fake)..bind(api());
    await until(() => controller.metrics != null);

    fake.hosts = [monitoringHostFixture(id: 'host_a', trust: 'revoked')];
    await controller.refresh(manual: false);
    await until(() => controller.inspectedHost?.trust == 'revoked');
    expect(controller.metrics, isNull);
    expect(controller.readingsLive, isFalse);
    expect(controller.readingsRestricted, isTrue);
  });

  test('offline keeps the earlier sample but stops calling it live', () async {
    final fake = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_a', trust: 'paired')];
    final controller = controllerFor(fake)..bind(api());
    await until(() => controller.metrics != null);
    fake.hosts = [
      monitoringHostFixture(
          id: 'host_a', trust: 'paired', connection: 'offline')
    ];
    await controller.refresh(manual: false);
    await until(() => controller.inspectedHost?.connectivity == 'offline');
    expect(controller.readingsLive, isFalse);
    expect(controller.staleGroups, contains('cpu'));
  });

  test('reconnect keeps one collector and one event subscription', () async {
    final fake = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_a', trust: 'local')];
    final source = api();
    final controller = DeviceMonitoringController(
        clock: () => DateTime.utc(2026, 10, 25, 6, 5),
        createClient: (_) => fake,
        eventDelay: Duration.zero,
        retryDelay: const Duration(hours: 1));
    addTearDown(controller.dispose);
    controller.bind(source);
    await until(() => controller.metrics != null);
    await controller.refresh(manual: false);
    await controller.refresh(manual: false);
    expect(fake.watches.length, 1);
    // Binding the same transport again is a no-op, not a second poller.
    controller.bind(source);
    expect(fake.watches.length, 1);
    expect(fake.closed, isFalse);
    await controller.shutdown();
    expect(fake.closed, isTrue);
  });

  testWidgets('details state the source, freshness and restricted access',
      (tester) async {
    await mountMonitoring(
        tester,
        MonitoringDetails(
            host: monitoringHostFixture(id: 'host_a', trust: 'paired'),
            report: honestReport('host_a'),
            clock: () => DateTime.utc(2026, 10, 25, 6, 5)));
    expect(find.text('Readings source'), findsOneWidget);
    expect(find.text('Collector report · current target'), findsOneWidget);
    expect(find.text('30s old'), findsOneWidget);
    expect(find.text('Execution / workspace'), findsOneWidget);
    expect(find.text('Not granted by monitoring'), findsOneWidget);
    // A denied collector group must not read as a zero value.
    expect(find.textContaining('not zero'), findsWidgets);

    await mountMonitoring(
        tester,
        MonitoringDetails(
            host: monitoringHostFixture(
                id: 'host_a', trust: 'paired', access: 'denied'),
            report: null));
    expect(find.textContaining('Monitoring permission was denied'),
        findsOneWidget);
    expect(find.text('None · telemetry permission withheld'), findsOneWidget);
  });

  testWidgets('device page shows real identity and capability boundaries',
      (tester) async {
    final session = DesktopSession(DemoApi(delay: Duration.zero));
    addTearDown(session.dispose);
    await tester.runAsync(session.start);
    await tester.runAsync(() async {
      await mountMonitoring(
          tester,
          DevicePage(
              device: 'host_a',
              host: monitoringHostFixture(
                  id: 'host_a', name: 'Workstation', trust: 'local'),
              controller: session));
    });
    expect(find.text('Local coordinator on this computer'), findsNWidgets(2));
    expect(find.text('Connection: online'), findsOneWidget);
    expect(find.text('Telemetry: allowed'), findsOneWidget);
    expect(find.text(monitoringExecutionNote), findsOneWidget);
    expect(find.text(monitoringWorkspaceNote), findsOneWidget);
    expect(find.byKey(const ValueKey('device-telemetry-chip')), findsOneWidget);
    await tester.runAsync(session.shutdown);
  });
}
