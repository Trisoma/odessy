import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// Keeps history requests bound to the current connection and selected filters.
class UsageHistoryController extends ChangeNotifier {
  UsageHistoryController(this.session) {
    session.addListener(_onSession);
    _onSession();
  }
  final DesktopSession session;
  Json? data;
  bool loading = false;
  String? error;
  int days = 30, offset = 0;
  String? provider, model;
  OdessyApi? _api;
  bool _connected = false, _disposed = false;
  int _epoch = 0, _lastReport = -1;
  String? _sessionId;

  void _onSession() {
    final api = session.api;
    final sid = session.session?.id;
    final lastReport = (session.session?.events ?? const <SessionEvent>[])
        .where((e) =>
            const {'cost', 'result', 'error', 'stopped'}.contains(e.type))
        .fold<int>(-1, (id, event) => event.id > id ? event.id : id);
    final changed = !identical(api, _api) || session.connected != _connected;
    if (changed) {
      _api = api;
      _connected = session.connected;
      data = null;
      error = null;
      ++_epoch;
      loading = false;
    }
    final newReport = lastReport != _lastReport || sid != _sessionId;
    _lastReport = lastReport;
    _sessionId = sid;
    if (session.connected && api is UsageHistoryApi && (changed || newReport)) {
      unawaited(refresh());
    } else if (changed && !_disposed) {
      notifyListeners();
    }
  }

  void setFilters(
      {int? range,
      String? selectedProvider,
      String? selectedModel,
      bool clearProvider = false,
      bool clearModel = false}) {
    days = range ?? days;
    provider = clearProvider ? null : selectedProvider ?? provider;
    model = clearModel ? null : selectedModel ?? model;
    offset = 0;
    unawaited(refresh());
  }

  void setOffset(int value) {
    offset = value < 0 ? 0 : value;
    unawaited(refresh());
  }

  Future<void> refresh() async {
    final api = session.api;
    if (!session.connected || api is! UsageHistoryApi || _disposed) return;
    final epoch = ++_epoch;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final result = await (api as UsageHistoryApi).usageHistory(
          days: days,
          provider: provider,
          model: model,
          limit: 20,
          offset: offset);
      if (_disposed || epoch != _epoch || !identical(api, session.api)) return;
      data = validateUsageHistory(result);
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        data = null;
        error =
            'Usage history is unavailable. Check the local service and try again.';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        loading = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    session.removeListener(_onSession);
    super.dispose();
  }
}
