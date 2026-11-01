import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/devices/controllers/device_monitoring_controller.dart';
import 'monitoring_presentation_test.dart';

class FakeMonitoring implements MonitoringClient {
  @override
  Uri get base => Uri.parse('http://127.0.0.1:1');
  final events = StreamController<MonitoringEvent>.broadcast(sync: true);
  List<MonitoringHost> hosts = [
    monitoringHostFixture(id: 'host_a', name: 'Same'),
    monitoringHostFixture(id: 'host_b', name: 'Same')
  ];
  Future<MonitoringSnapshot> Function(String?)? pages;
  Future<Json> Function(String)? metricReply;
  Future<Json> Function(String, DateTime, DateTime, int)? historyReply;
  Object? hostError, overviewError;
  Future<MonitoringHost> Function(String)? hostReply;
  final cursors = <String?>[], watches = <String?>[];
  final histories = <({String id, DateTime from, DateTime to, int step})>[];
  int sequence = 0, metricsCalls = 0;
  bool closed = false;
  bool brokenUpdates = false;
  @override
  Future<MonitoringSnapshot> listHosts({String? cursor, int limit = 20}) {
    cursors.add(cursor);
    return pages?.call(cursor) ??
        Future.value(MonitoringSnapshot(hosts, 'epoch:$sequence', null));
  }

  @override
  Future<MonitoringHost> getHost(String id) async {
    if (hostError != null) throw hostError!;
    if (hostReply != null) return hostReply!(id);
    return hosts.firstWhere((h) => h.id == id);
  }

  @override
  Future<Json> getMetrics(String id) {
    metricsCalls++;
    return metricReply?.call(id) ??
        Future.value(monitoringReportFixture(id: id));
  }

  @override
  Future<Json> getOverview() async {
    if (overviewError != null) throw overviewError!;
    return {'schema_version': 1, 'history_degrade': ''};
  }

  Json response(String id, DateTime from, DateTime to, int step) => {
        'schema_version': 1,
        'host_id': id,
        'from': from.toIso8601String(),
        'to': to.toIso8601String(),
        'step_seconds': step,
        'series': []
      };
  @override
  Future<Json> getHistory(String id,
      {required DateTime from,
      required DateTime to,
      int step = 60,
      String series = 'cpu.utilization_percent'}) {
    histories.add((id: id, from: from, to: to, step: step));
    return historyReply?.call(id, from, to, step) ??
        Future.value(response(id, from, to, step));
  }

  @override
  Stream<MonitoringEvent> watchEvents({String? after}) {
    watches.add(after);
    return brokenUpdates
        ? Stream.error(StateError('fixture disconnect'))
        : events.stream;
  }

  void emit(String type, {String id = 'host_a'}) {
    events.add(MonitoringEvent(
        'epoch:${++sequence}', type, {'schema_version': 1, 'host_id': id}));
  }

  @override
  void close() {
    if (closed) return;
    closed = true;
    unawaited(events.close());
  }
}

Future<void> until(bool Function() condition) async {
  final deadline = DateTime.now().add(const Duration(seconds: 3));
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) fail('Controller did not settle');
    await Future<void>.delayed(const Duration(milliseconds: 1));
  }
}

void main() {
  DeviceMonitoringController controllerFor(FakeMonitoring fake) {
    final controller = DeviceMonitoringController(
        clock: () => DateTime.utc(2026, 10, 26, 12),
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

  test('completes pinned pagination and keeps duplicate display names distinct',
      () async {
    final fake = FakeMonitoring();
    fake.pages = (cursor) async => MonitoringSnapshot(
        [fake.hosts[cursor == null ? 0 : 1]],
        'epoch:0',
        cursor == null ? 'page:2' : null);
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    expect(controller.hosts.map((h) => h.id), ['host_a', 'host_b']);
    expect(fake.cursors, [null, 'page:2']);
    expect(fake.watches, ['epoch:0']);
    controller.select('host_b');
    await until(() => controller.metrics?['host_id'] == 'host_b');
    expect(controller.selectedId, 'host_b');
  });
  test('rejects pagination loops and inconsistent snapshot cursors', () async {
    for (final loop in [true, false]) {
      final fake = FakeMonitoring();
      fake.pages = (cursor) async => MonitoringSnapshot(
          [],
          cursor == null
              ? 'epoch:0'
              : loop
                  ? 'epoch:0'
                  : 'other:1',
          'repeat');
      final controller = controllerFor(fake)..bind(api());
      await until(() => !controller.loadingInventory);
      expect(controller.inventoryError, isNotNull);
      expect(controller.hosts, isEmpty);
    }
  });
  test('empty inventory creates no synthetic local host', () async {
    final fake = FakeMonitoring()..hosts = [];
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    expect(controller.hosts, isEmpty);
    expect(controller.selectedHost, isNull);
    expect(fake.metricsCalls, 0);
  });

  test('background refresh preserves current data without marking it stale',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(
        () => !controller.loadingInventory && controller.metrics != null);

    final pendingInventory = Completer<MonitoringSnapshot>();
    fake.pages = (_) => pendingInventory.future;
    final inventory = controller.refresh();
    expect(controller.loadingInventory, isTrue);
    expect(controller.inventoryRetained, isFalse);
    expect(controller.hosts, hasLength(2));
    pendingInventory.complete(MonitoringSnapshot(fake.hosts, 'epoch:0', null));
    await inventory;

    final pendingMetrics = Completer<Json>();
    fake.metricReply = (_) => pendingMetrics.future;
    final metrics = controller.refreshSelected();
    await until(() => controller.loadingMetrics);
    expect(controller.metricsRetained, isFalse);
    expect(controller.metrics, isNotNull);
    pendingMetrics.complete(monitoringReportFixture());
    await metrics;
    expect(controller.metricsRetained, isFalse);
  });
  for (final status in [401, 403]) {
    test('event stream HTTP $status clears cached hosts and telemetry',
        () async {
      final fake = FakeMonitoring();
      final controller = controllerFor(fake)..bind(api());
      await until(
          () => controller.metrics != null && controller.history != null);
      fake.events.addError(MonitoringFailure(status));
      await until(() => controller.inventoryError != null);
      expect(controller.hosts, isEmpty);
      expect(controller.selectedId, isNull);
      expect(controller.metrics, isNull);
      expect(controller.history, isNull);
      expect(controller.inventoryError, contains('$status'));
    });

    test('inventory HTTP $status clears cached hosts and telemetry', () async {
      final fake = FakeMonitoring();
      final controller = controllerFor(fake)..bind(api());
      await until(
          () => controller.metrics != null && controller.history != null);
      fake.pages = (_) async => throw MonitoringFailure(status);
      await controller.refresh();
      expect(controller.hosts, isEmpty);
      expect(controller.selectedId, isNull);
      expect(controller.metrics, isNull);
      expect(controller.history, isNull);
      expect(controller.inventoryError, contains('$status'));
    });

    test('overview HTTP $status hides cached readings', () async {
      final fake = FakeMonitoring();
      final controller = controllerFor(fake)..bind(api());
      await until(
          () => controller.metrics != null && controller.history != null);
      fake.overviewError = MonitoringFailure(status);
      await controller.refresh();
      expect(controller.metrics, isNull);
      expect(controller.history, isNull);
      expect(controller.inspectedHost, isNull);
    });

    test('metrics HTTP $status hides cached readings and pending history',
        () async {
      final fake = FakeMonitoring();
      final controller = controllerFor(fake)..bind(api());
      await until(
          () => controller.metrics != null && controller.history != null);
      final pending = Completer<Json>();
      fake.historyReply = (_, __, ___, ____) => pending.future;
      final previousCalls = fake.histories.length;
      final history = controller.refreshHistory();
      await until(() => fake.histories.length > previousCalls);
      final query = fake.histories.last;
      fake.metricReply = (_) async => throw MonitoringFailure(status);
      final refresh = controller.refreshSelected();
      await until(() => controller.metricsError != null);
      expect(controller.metrics, isNull);
      expect(controller.history, isNull);
      expect(controller.inspectedHost, isNull);
      expect(controller.loadingHistory, isFalse);
      pending
          .complete(fake.response(query.id, query.from, query.to, query.step));
      await history;
      await refresh;
      expect(controller.history, isNull);
    });

    test('history HTTP $status hides cached readings and pending metrics',
        () async {
      final fake = FakeMonitoring();
      final controller = controllerFor(fake)..bind(api());
      await until(
          () => controller.metrics != null && controller.history != null);
      final pending = Completer<Json>();
      fake.metricReply = (_) => pending.future;
      final previousCalls = fake.metricsCalls;
      final metrics = controller.refreshSelected();
      await until(() => fake.metricsCalls > previousCalls);
      fake.historyReply =
          (_, __, ___, ____) async => throw MonitoringFailure(status);
      await controller.refreshHistory();
      expect(controller.metrics, isNull);
      expect(controller.history, isNull);
      expect(controller.inspectedHost, isNull);
      pending.complete(monitoringReportFixture(id: 'host_a'));
      await metrics;
      expect(controller.metrics, isNull);
    });
  }
  test('late metrics from previous host cannot replace current selection',
      () async {
    final pending = Completer<Json>();
    final fake = FakeMonitoring();
    fake.metricReply = (id) => id == 'host_a'
        ? pending.future
        : Future.value(monitoringReportFixture(id: id));
    final controller = controllerFor(fake)..bind(api());
    await until(() => fake.metricsCalls == 1);
    controller.select('host_b');
    await until(() => controller.metrics?['host_id'] == 'host_b');
    pending.complete(monitoringReportFixture(id: 'host_a'));
    await until(() => !controller.loadingInventory);
    expect(controller.metrics?['host_id'], 'host_b');
  });
  test(
      'origin replacement clears data and rejects pending previous-origin response',
      () async {
    final pending = Completer<Json>();
    final first = FakeMonitoring()..metricReply = (_) => pending.future;
    final second = FakeMonitoring()
      ..hosts = [monitoringHostFixture(id: 'host_new')];
    final controller = DeviceMonitoringController(
        createClient: (api) => api.base.port == 1 ? first : second,
        retryDelay: const Duration(hours: 1));
    addTearDown(controller.dispose);
    controller.bind(api());
    await until(() => first.metricsCalls == 1);
    controller.bind(api(2));
    expect(controller.metrics, isNull);
    expect(first.closed, true);
    await until(() => controller.metrics?['host_id'] == 'host_new');
    pending.complete(monitoringReportFixture(id: 'host_a'));
    await Future<void>.delayed(Duration.zero);
    expect(controller.metrics?['host_id'], 'host_new');
    expect(controller.hosts.single.id, 'host_new');
  });
  test('old history range is rejected after choosing a new preset', () async {
    final old = Completer<Json>();
    final fake = FakeMonitoring();
    fake.historyReply = (id, from, to, step) => step == 60
        ? old.future
        : Future.value(fake.response(id, from, to, step));
    final controller = controllerFor(fake)..bind(api());
    await until(() => fake.histories.isNotEmpty);
    final first = fake.histories.first;
    controller.setWindow(HistoryWindow.week);
    await until(() => controller.history?['step_seconds'] == 900);
    old.complete(fake.response(first.id, first.from, first.to, first.step));
    await until(() => !controller.loadingInventory);
    expect(controller.history?['step_seconds'], 900);
    expect(controller.historyFrom, DateTime.utc(2026, 10, 19, 12));
  });
  test(
      'trust invalidation hides readings before refresh and rejects in-flight history',
      () async {
    final pending = Completer<Json>();
    final fake = FakeMonitoring()
      ..historyReply = (id, from, to, step) => pending.future;
    final controller = controllerFor(fake)..bind(api());
    await until(() => fake.histories.isNotEmpty);
    final query = fake.histories.first;
    fake.hosts = [
      monitoringHostFixture(id: 'host_a', trust: 'revoked', access: 'denied')
    ];
    fake.emit('host.trust_changed');
    expect(controller.inspectedHost, isNull);
    expect(controller.metrics, isNull);
    pending.complete(fake.response(query.id, query.from, query.to, query.step));
    await until(() => controller.inspectedHost?.trust == 'revoked');
    expect(controller.history, isNull);
    expect(controller.metrics, isNull);
  });
  test('resync gets a fresh pinned snapshot and restarts event position',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    fake.emit('resync_required');
    await until(() => fake.watches.length == 2);
    expect(fake.watches.last, 'epoch:1');
    expect(controller.inventoryError, isNull);
  });
  test('ordinary host updates retain readings and do not cancel history',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    final pending = Completer<Json>();
    fake.historyReply = (id, from, to, step) => pending.future;
    final request = controller.refreshHistory();
    await until(() => fake.histories.length == 2);
    final query = fake.histories.last;
    fake.emit('host.updated');
    expect(controller.inspectedHost?.id, 'host_a');
    expect(controller.metrics, isNotNull);
    expect(controller.history, isNotNull);
    expect(controller.loadingHistory, true);
    pending.complete(fake.response(query.id, query.from, query.to, query.step));
    await request;
    expect(controller.history, isNotNull);
    expect(controller.loadingHistory, false);
  });
  test('access denial ends pending history loading and permits later recovery',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    final pending = Completer<Json>();
    fake.historyReply = (id, from, to, step) => pending.future;
    final reading = controller.refreshHistory();
    await until(() => controller.loadingHistory && fake.histories.length == 2);
    final query = fake.histories.last;
    fake.hosts = [monitoringHostFixture(id: 'host_a', access: 'denied')];
    await controller.refreshSelected();
    expect(controller.loadingHistory, false);
    expect(controller.history, isNull);
    pending.complete(fake.response(query.id, query.from, query.to, query.step));
    await reading;
    expect(controller.history, isNull);
    fake.hosts = [monitoringHostFixture(id: 'host_a')];
    fake.historyReply = null;
    await controller.refreshSelected();
    expect(controller.history, isNotNull);
    expect(controller.loadingHistory, false);
  });
  test('permission event invalidates an already pending inventory snapshot',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    final stale = Completer<MonitoringSnapshot>();
    final fresh = Completer<MonitoringSnapshot>();
    var reads = 0;
    fake.pages = (_) => ++reads == 1 ? stale.future : fresh.future;
    final refresh = controller.refresh();
    await until(() => reads == 1);
    final previousHosts = fake.hosts;
    fake.hosts = [monitoringHostFixture(id: 'host_a', access: 'denied')];
    fake.emit('host.access_changed');
    stale.complete(MonitoringSnapshot(previousHosts, 'epoch:0', null));
    await refresh;
    expect(controller.inspectedHost, isNull);
    expect(controller.metrics, isNull);
    await until(() => reads == 2);
    fresh.complete(MonitoringSnapshot(fake.hosts, 'epoch:1', null));
    await until(() => !controller.loadingInventory);
    expect(controller.inspectedHost?.access, 'denied');
    expect(controller.history, isNull);
  });
  test('restricted inventory hides old readings before host verification ends',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    final pending = Completer<MonitoringHost>();
    var verifying = false;
    fake.hosts = [monitoringHostFixture(id: 'host_a', access: 'denied')];
    fake.hostReply = (_) {
      verifying = true;
      return pending.future;
    };
    final refresh = controller.refresh();
    await until(() => verifying);
    expect(controller.metrics, isNull);
    expect(controller.history, isNull);
    expect(controller.inspectedHost?.access, 'denied');
    pending.complete(fake.hosts.single);
    await refresh;
  });
  test('burst events coalesce rather than starting parallel inventory reads',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    final before = fake.cursors.length;
    for (var i = 0; i < 30; i++) {
      fake.emit('host.metrics');
    }
    await until(() => fake.cursors.length > before);
    await until(() => !controller.loadingInventory);
    expect(fake.cursors.length, before + 1);
  });
  test(
      'invalid bindings and transport diagnostics never expose unrelated data/secrets',
      () async {
    final fake = FakeMonitoring()
      ..metricReply = (_) async => monitoringReportFixture(id: 'host_wrong');
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    expect(controller.metrics, isNull);
    expect(controller.metricsError, contains('invalid'));
    fake.hostError = StateError('Bearer private-diagnostic');
    await controller.refreshSelected();
    expect(controller.metricsError, isNot(contains('private-diagnostic')));
    expect(controller.history, isNull);
  });
  test(
      'overview failure discloses unknown storage health without hiding metrics',
      () async {
    final fake = FakeMonitoring()
      ..overviewError = StateError('private diagnostic');
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    expect(controller.metrics, isNotNull);
    expect(controller.degradation, 'History storage status is unavailable.');
  });
  test('disconnect marks retained data and shutdown closes owned resources',
      () async {
    final fake = FakeMonitoring();
    final controller = controllerFor(fake)..bind(api());
    await until(() => !controller.loadingInventory);
    await fake.events.close();
    await until(() => controller.inventoryError != null);
    expect(controller.metricsRetained, true);
    expect(controller.historyRetained, true);
    await controller.shutdown();
    expect(fake.closed, true);
  });
  test('repeated SSE failure retries a bounded number of times', () async {
    final fake = FakeMonitoring()..brokenUpdates = true;
    final controller = DeviceMonitoringController(
        createClient: (_) => fake, retryDelay: const Duration(milliseconds: 1));
    addTearDown(controller.dispose);
    controller.bind(api());
    await until(() => fake.watches.length == 6);
    await Future<void>.delayed(const Duration(milliseconds: 80));
    expect(fake.watches.length, 6);
    expect(controller.inventoryError, isNotNull);
  });
}
