import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

class MonitorController extends ChangeNotifier {
  OdessyApi? _api;
  MonitoringClient? _client;
  StreamSubscription<MonitoringEvent>? _events;
  Timer? _timer;
  int _epoch = 0;
  bool _disposed = false, busy = false;
  String message =
      'Connect to a live Odessy service to view monitoring. Demo hosts are not real telemetry.';
  List<MonitoringHost> hosts = [];
  String? selected;
  Json? metrics, history;
  String historyMessage = '';
  DateTime? updated;
  void bind(OdessyApi api) {
    if (identical(_api, api)) return;
    _api = api;
    _epoch++;
    _events?.cancel();
    _events = null;
    _timer?.cancel();
    _client?.close();
    _client = null;
    hosts = [];
    selected = null;
    metrics = null;
    history = null;
    updated = null;
    busy = false;
    message =
        'Connect to a live Odessy service to view monitoring. Demo hosts are not real telemetry.';
    if (api is LocalApi) {
      _client = api.createMonitoringClient();
      _timer = Timer.periodic(const Duration(seconds: 5), (_) => refresh());
      refresh();
    } else {
      notifyListeners();
    }
  }

  Future<void> select(String id) async {
    selected = id;
    metrics = null;
    history = null;
    notifyListeners();
    if (!busy) await refresh();
  }

  Future<void> refresh() async {
    final client = _client;
    if (client == null || busy || _disposed) return;
    final epoch = _epoch;
    busy = true;
    notifyListeners();
    try {
      final all = <MonitoringHost>[];
      var snapshot = await client.listHosts();
      final cursor = snapshot.cursor;
      all.addAll(snapshot.hosts);
      var pages = 1;
      while (snapshot.nextCursor != null && pages++ < 32) {
        snapshot = await client.listHosts(cursor: snapshot.nextCursor);
        all.addAll(snapshot.hosts);
      }
      if (_disposed || epoch != _epoch) return;
      hosts = all;
      if (!all.any((h) => h.id == selected)) {
        selected = all.isEmpty ? null : all.first.id;
      }
      final id = selected;
      final detail = id == null ? null : await client.getMetrics(id);
      Json? past;
      var historyError = '';
      if (id != null) {
        try {
          final now = DateTime.now().toUtc();
          past = await client.getHistory(id,
              from: now.subtract(const Duration(hours: 1)), to: now);
        } catch (_) {
          historyError = 'History unavailable or storage degraded.';
        }
      }
      if (_disposed || epoch != _epoch || id != selected) return;
      metrics = detail;
      history = past;
      historyMessage = historyError;
      updated = DateTime.now();
      message = all.isEmpty
          ? 'No hosts available. Check telemetry configuration and grants.'
          : '';
      _events ??= client.watchEvents(after: cursor).listen((_) {
        refresh();
      }, onError: (Object _) {
        _events?.cancel();
        _events = null;
      }, onDone: () {
        _events = null;
      });
    } catch (e) {
      if (_disposed || epoch != _epoch) return;
      message = e is MonitoringFailure
          ? 'Monitoring unavailable (HTTP ${e.status}). Enable monitoring on the service and check access. Retained values are historical.'
          : 'Monitoring connection unavailable. Retained values are historical; reconnect or refresh.';
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    _timer?.cancel();
    _events?.cancel();
    _client?.close();
    super.dispose();
  }
}
