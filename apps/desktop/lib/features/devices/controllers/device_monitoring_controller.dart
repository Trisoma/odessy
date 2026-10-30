import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import 'demo_monitoring.dart';

enum HistoryWindow {
  quarterHour('Last 15 minutes', Duration(minutes: 15), 60),
  hour('Last hour', Duration(hours: 1), 60),
  day('Last 24 hours', Duration(days: 1), 300),
  week('Last 7 days', Duration(days: 7), 900);

  const HistoryWindow(this.label, this.duration, this.step);
  final String label;
  final Duration duration;
  final int step;
}

/// Read-only state. Inspection never invokes session/execution commands.
class DeviceMonitoringController extends ChangeNotifier {
  DeviceMonitoringController(
      {DateTime Function()? clock,
      MonitoringClient Function(LocalApi)? createClient,
      this.retryDelay = const Duration(seconds: 2),
      this.eventDelay = const Duration(milliseconds: 150)})
      : clock = clock ?? DateTime.now,
        createClient = createClient ?? ((api) => api.createMonitoringClient());
  final DateTime Function() clock;
  final MonitoringClient Function(LocalApi) createClient;
  final Duration retryDelay, eventDelay;
  OdessyApi? _api;
  MonitoringClient? _client;
  StreamSubscription<MonitoringEvent>? _events;
  Timer? _retry, _debounce;
  bool _disposed = false, _queued = false, _restart = false;
  int _binding = 0, _selection = 0, _range = 0, _attempts = 0;
  int _invalidation = 0;
  Object? _inventoryTicket, _detailTicket, _historyTicket;
  Future<void>? _inventoryTask, _detailTask, _historyTask, _shutdown;
  String? _cursor;
  List<MonitoringHost> hosts = const [];
  String? selectedId, inventoryError, metricsError, historyError, degradation;
  MonitoringHost? _verified;
  Json? metrics, history;
  bool loadingInventory = false, loadingMetrics = false, loadingHistory = false;
  bool inventoryRetained = false,
      metricsRetained = false,
      historyRetained = false;
  HistoryWindow window = HistoryWindow.hour;
  DateTime? historyFrom, historyTo;
  bool get demo => _api?.isDemo == true;
  MonitoringHost? get selectedHost {
    for (final host in hosts) {
      if (host.id == selectedId) return host;
    }
    return null;
  }

  MonitoringHost? get inspectedHost => _verified;
  bool _current(MonitoringClient client, int binding) =>
      !_disposed && identical(client, _client) && binding == _binding;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  String _error(Object error) => error is MonitoringFailure
      ? 'Monitoring unavailable (HTTP ${error.status}).'
      : error is FormatException
          ? 'The monitoring response was invalid.'
          : 'Monitoring is unavailable. Refresh to retry.';
  bool _accessDenied(Object error) =>
      error is MonitoringFailure &&
      (error.status == 401 || error.status == 403);
  void _clearDeniedReadings(Object error) {
    _invalidate();
    metricsError = _error(error);
    historyError = _error(error);
    metricsRetained = false;
    historyRetained = false;
    _notify();
  }

  void _clearDeniedInventory(Object error) {
    _clearDeniedReadings(error);
    hosts = const [];
    _changeSelection(null);
    inventoryRetained = false;
  }

  Future<void> _cancel(
      StreamSubscription<MonitoringEvent>? subscription) async {
    try {
      await subscription?.cancel().timeout(const Duration(seconds: 2));
    } catch (_) {
      // Client/socket closure and epochs already prevent stale delivery.
    }
  }

  void bind(OdessyApi api) {
    if (_disposed || identical(_api, api)) return;
    ++_binding;
    ++_selection;
    ++_range;
    _retry?.cancel();
    _debounce?.cancel();
    _attempts = 0;
    final old = _events;
    _events = null;
    _client?.close();
    _client = null;
    if (old != null) unawaited(_cancel(old));
    _api = api;
    hosts = const [];
    selectedId = null;
    _verified = null;
    metrics = null;
    history = null;
    inventoryError = null;
    metricsError = null;
    historyError = null;
    degradation = null;
    inventoryRetained = false;
    metricsRetained = false;
    historyRetained = false;
    loadingInventory = false;
    loadingMetrics = false;
    loadingHistory = false;
    _inventoryTask = null;
    _detailTask = null;
    _historyTask = null;
    _inventoryTicket = null;
    _detailTicket = null;
    _historyTicket = null;
    _queued = false;
    _restart = false;
    _cursor = null;
    historyFrom = null;
    historyTo = null;
    try {
      _client = api.isDemo
          ? DemoMonitoring(clock)
          : api is LocalApi
              ? createClient(api)
              : null;
      if (_client == null) {
        inventoryError = 'Monitoring is not available for this connection.';
      }
    } catch (error) {
      inventoryError = _error(error);
    }
    _notify();
    if (_client != null) unawaited(refresh());
  }

  Future<void> refresh({bool restartEvents = false, bool manual = true}) {
    if (_disposed || _client == null) return Future.value();
    if (manual) {
      _attempts = 0;
      _retry?.cancel();
    }
    _restart |= restartEvents;
    if (_inventoryTask != null) {
      _queued = true;
      return _inventoryTask!;
    }
    final client = _client!, binding = _binding, ticket = Object();
    _inventoryTicket = ticket;
    loadingInventory = true;
    _notify();
    return _inventoryTask =
        Future<void>.microtask(() => _loadInventory(client, binding, ticket));
  }

  Future<void> _loadInventory(
      MonitoringClient client, int binding, Object ticket) async {
    final invalidation = _invalidation;
    try {
      final fresh = <MonitoringHost>[];
      final pages = <String>{};
      final ids = <String>{};
      String? next, position;
      do {
        if (pages.length >= 128) {
          throw const FormatException('Inventory page limit');
        }
        final snapshot = await client.listHosts(cursor: next, limit: 32);
        if (!_current(client, binding)) return;
        position ??= snapshot.cursor;
        if (snapshot.cursor != position ||
            !RegExp(r'^[A-Za-z0-9_-]{1,128}:\d{1,20}$').hasMatch(position)) {
          throw const FormatException('Inventory cursor changed');
        }
        for (final host in snapshot.hosts) {
          if (!ids.add(host.id)) {
            throw const FormatException('Duplicate host ID');
          }
          fresh.add(host);
        }
        next = snapshot.nextCursor;
        if (next != null && !pages.add(next)) {
          throw const FormatException('Pagination loop');
        }
      } while (next != null);
      if (!_current(client, binding)) return;
      // An access/trust/resync event can arrive while pagination is in flight.
      // Never publish that older snapshot or rewind its event position.
      if (invalidation != _invalidation) {
        _queued = true;
        return;
      }
      hosts = List.unmodifiable(fresh);
      inventoryError = null;
      inventoryRetained = false;
      _cursor = position;
      if (selectedHost == null) {
        _changeSelection(hosts.isEmpty ? null : hosts.first.id);
      } else if (selectedHost!.access != 'allowed' ||
          selectedHost!.trust == 'revoked') {
        // The inventory itself is sufficient evidence to hide old readings;
        // do not leave them visible while getHost is pending.
        final restricted = selectedHost!;
        _invalidate();
        _verified = restricted;
      }
      final restart = _restart;
      _restart = false;
      if (!demo && (_events == null || restart)) await _listen(client, binding);
      if (!_current(client, binding)) return;
      _notify();
      await Future.wait([_overview(client, binding), refreshSelected()]);
    } catch (error) {
      if (_current(client, binding)) {
        if (_accessDenied(error)) {
          _clearDeniedInventory(error);
        }
        inventoryError = _error(error);
        inventoryRetained = hosts.isNotEmpty;
        metricsRetained = metrics != null;
        historyRetained = history != null;
        _scheduleRetry(client, binding);
      }
    } finally {
      if (_current(client, binding) && identical(_inventoryTicket, ticket)) {
        loadingInventory = false;
        _inventoryTask = null;
        _notify();
        if (_queued) {
          _queued = false;
          unawaited(refresh(manual: false));
        }
      }
    }
  }

  Future<void> _overview(MonitoringClient client, int binding) async {
    try {
      final response = await client.getOverview();
      if (!_current(client, binding)) return;
      final value = response['history_degrade'];
      if (response['schema_version'] != 1 ||
          (value != null && value is! String)) {
        throw const FormatException('Invalid overview');
      }
      // Do not display arbitrary server diagnostics which might contain secrets.
      degradation = value is String && value.isNotEmpty
          ? 'The monitoring service reports degraded history storage.'
          : null;
    } catch (error) {
      if (_current(client, binding)) {
        if (_accessDenied(error)) _clearDeniedReadings(error);
        degradation = 'History storage status is unavailable.';
      }
    }
    if (_current(client, binding)) _notify();
  }

  void select(String id) {
    if (_disposed || selectedId == id || !hosts.any((h) => h.id == id)) return;
    _changeSelection(id);
    _notify();
    unawaited(refreshSelected());
  }

  void _changeSelection(String? id) {
    ++_selection;
    ++_range;
    selectedId = id;
    _verified = null;
    metrics = null;
    history = null;
    metricsError = null;
    historyError = null;
    metricsRetained = false;
    historyRetained = false;
    loadingMetrics = false;
    loadingHistory = false;
    _detailTask = null;
    _historyTask = null;
    _detailTicket = null;
    _historyTicket = null;
    _newRange();
  }

  void _newRange() {
    historyTo = clock().toUtc();
    historyFrom = historyTo!.subtract(window.duration);
  }

  void setWindow(HistoryWindow value) {
    if (_disposed || window == value) return;
    window = value;
    ++_range;
    _historyTask = null;
    _historyTicket = null;
    _newRange();
    historyRetained = history != null;
    _notify();
    unawaited(refreshHistory(advance: false));
  }

  Future<void> refreshSelected() {
    final client = _client, id = selectedId;
    if (_disposed || client == null || id == null) return Future.value();
    if (_detailTask != null) return _detailTask!;
    final ticket = Object();
    _detailTicket = ticket;
    loadingMetrics = true;
    _notify();
    final binding = _binding, selection = _selection;
    return _detailTask = Future<void>.microtask(
        () => _loadSelected(client, binding, selection, id, ticket));
  }

  bool _selected(
          MonitoringClient client, int binding, int selection, String id) =>
      _current(client, binding) && selection == _selection && id == selectedId;
  Future<void> _loadSelected(MonitoringClient client, int binding,
      int selection, String id, Object ticket) async {
    try {
      final host = await client.getHost(id);
      if (!_selected(client, binding, selection, id)) return;
      if (host.id != id) throw const FormatException('Wrong host');
      _verified = host;
      hosts = List.unmodifiable(hosts.map((h) => h.id == id ? host : h));
      if (host.access != 'allowed' || host.trust == 'revoked') {
        _invalidate();
        _verified = host;
        metricsError = null;
        historyError = null;
        _notify();
      } else {
        await Future.wait([
          _metrics(client, binding, selection, id),
          if (history == null || historyRetained) refreshHistory(advance: false)
        ]);
      }
    } catch (error) {
      if (_selected(client, binding, selection, id)) {
        _invalidate();
        metricsError = _error(error);
        historyError = _error(error);
        _notify();
      }
    } finally {
      if (_selected(client, binding, selection, id) &&
          identical(ticket, _detailTicket)) {
        loadingMetrics = false;
        _detailTask = null;
        _notify();
      }
    }
  }

  Future<void> _metrics(
      MonitoringClient client, int binding, int selection, String id) async {
    try {
      final response = await client.getMetrics(id);
      if (!_selected(client, binding, selection, id)) return;
      if (response['schema_version'] != 1 || response['host_id'] != id) {
        throw const FormatException('Wrong metrics');
      }
      metrics = response;
      metricsError = null;
      metricsRetained = false;
    } catch (error) {
      if (_selected(client, binding, selection, id)) {
        if (_accessDenied(error)) {
          _clearDeniedReadings(error);
        } else {
          metricsError = _error(error);
          metricsRetained = metrics != null;
        }
      }
    }
  }

  Future<void> refreshHistory({bool advance = true}) {
    final client = _client, host = _verified;
    if (_disposed ||
        client == null ||
        host == null ||
        host.access != 'allowed' ||
        host.trust == 'revoked') {
      return Future.value();
    }
    if (advance) {
      ++_range;
      _historyTask = null;
      _historyTicket = null;
      _newRange();
    }
    if (_historyTask != null) return _historyTask!;
    final ticket = Object();
    _historyTicket = ticket;
    loadingHistory = true;
    historyRetained = history != null;
    _notify();
    final binding = _binding,
        selection = _selection,
        range = _range,
        from = historyFrom!,
        to = historyTo!,
        step = window.step;
    return _historyTask = Future<void>.microtask(() => _loadHistory(
        client, binding, selection, range, host.id, from, to, step, ticket));
  }

  Future<void> _loadHistory(
      MonitoringClient client,
      int binding,
      int selection,
      int range,
      String id,
      DateTime from,
      DateTime to,
      int step,
      Object ticket) async {
    bool current() =>
        _selected(client, binding, selection, id) && range == _range;
    try {
      final response = await client.getHistory(id,
          from: from,
          to: to,
          step: step,
          series:
              'cpu.utilization_percent,memory.used_bytes,memory.total_bytes,memory.utilization_percent,gpu.utilization_percent,gpu.memory_used_bytes');
      if (!current()) return;
      if (response['schema_version'] != 1 ||
          response['host_id'] != id ||
          response['step_seconds'] != step ||
          DateTime.tryParse('${response['from']}') != from ||
          DateTime.tryParse('${response['to']}') != to) {
        throw const FormatException('Wrong history binding');
      }
      history = response;
      historyError = null;
      historyRetained = false;
    } catch (error) {
      if (current()) {
        if (_accessDenied(error)) {
          _clearDeniedReadings(error);
        } else {
          historyError = _error(error);
          historyRetained = history != null;
        }
      }
    } finally {
      if (current() && identical(ticket, _historyTicket)) {
        loadingHistory = false;
        _historyTask = null;
        _notify();
      }
    }
  }

  Future<void> _listen(MonitoringClient client, int binding) async {
    final old = _events;
    _events = null;
    await _cancel(
        old); // Transport closes its SSE socket before awaiting cancellation.
    if (!_current(client, binding)) return;
    _events = client.watchEvents(after: _cursor).listen((event) {
      if (!_current(client, binding)) return;
      _attempts = 0;
      if (event.requiresResync) {
        _invalidate();
        _queue(client, binding, restart: true);
        return;
      }
      if (event.id != null) {
        final before = _cursor?.split(':'), after = event.id!.split(':');
        if (before != null &&
            before[0] == after[0] &&
            BigInt.parse(after[1]) <= BigInt.parse(before[1])) {
          return;
        }
        if (before != null && before[0] != after[0]) {
          _invalidate();
          _queue(client, binding, restart: true);
          return;
        }
        _cursor = event.id;
      }
      // The service emits host.updated for unchanged inventory and runtime
      // status. Trust/access changes have their own events; ordinary updates
      // still refresh the snapshot but must not cancel in-flight history.
      if (event.type != 'host.metrics' && event.type != 'host.updated') {
        _invalidate();
      }
      _queue(client, binding);
    }, onError: (Object error) {
      if (_current(client, binding)) _lost(client, binding, error: error);
    }, onDone: () {
      if (_current(client, binding)) _lost(client, binding);
    }, cancelOnError: true);
  }

  void _invalidate() {
    ++_invalidation;
    ++_selection;
    ++_range;
    _verified = null;
    metrics = null;
    history = null;
    _detailTask = null;
    _historyTask = null;
    _detailTicket = null;
    _historyTicket = null;
    loadingMetrics = false;
    loadingHistory = false;
    inventoryRetained = hosts.isNotEmpty;
    _notify();
  }

  void _queue(MonitoringClient client, int binding, {bool restart = false}) {
    _restart |= restart;
    if (_debounce?.isActive == true) return;
    _debounce = Timer(eventDelay, () {
      if (_current(client, binding)) unawaited(refresh(manual: false));
    });
  }

  void _lost(MonitoringClient client, int binding, {Object? error}) {
    final old = _events;
    _events = null;
    if (old != null) unawaited(_cancel(old));
    if (error != null && _accessDenied(error)) _clearDeniedInventory(error);
    inventoryError = error != null && _accessDenied(error)
        ? _error(error)
        : 'Live monitoring updates disconnected. Refresh to retry.';
    inventoryRetained = hosts.isNotEmpty;
    metricsRetained = metrics != null;
    historyRetained = history != null;
    _scheduleRetry(client, binding);
    _notify();
  }

  void _scheduleRetry(MonitoringClient client, int binding) {
    if (demo || _attempts >= 5 || _retry?.isActive == true) return;
    final delay = retryDelay * (1 << _attempts++);
    _retry = Timer(delay, () {
      if (_current(client, binding)) {
        unawaited(refresh(restartEvents: true, manual: false));
      }
    });
  }

  Future<void> shutdown() => _shutdown ??= _close();
  Future<void> _close() async {
    _disposed = true;
    ++_binding;
    ++_selection;
    ++_range;
    _retry?.cancel();
    _debounce?.cancel();
    final subscription = _events;
    _events = null;
    _client?.close();
    _client = null;
    await _cancel(subscription);
  }

  @override
  void dispose() {
    unawaited(shutdown());
    super.dispose();
  }
}
