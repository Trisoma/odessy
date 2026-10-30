import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

class RuntimeInventoryController extends ChangeNotifier {
  RuntimeInventoryController(this.session) {
    session.addListener(_bind);
    _bind();
  }
  final DesktopSession session;
  OdessyApi? _api;
  bool _connected = false, _disposed = false;
  int _epoch = 0;
  bool loading = false;
  String? error, instanceId;
  List<Json> settings = const [];
  bool get available => _connected && _api is RuntimeSettingsApi;

  void _bind() {
    final api = session.api, connected = session.connected;
    if (identical(api, _api) && connected == _connected) return;
    _api = api;
    _connected = connected;
    ++_epoch;
    loading = false;
    error = null;
    instanceId = null;
    settings = const [];
    notifyListeners();
    if (available) unawaited(refresh());
  }

  Future<void> refresh() async {
    if (_disposed || !available || loading) return;
    final api = _api!, epoch = _epoch;
    bool current() =>
        !_disposed &&
        epoch == _epoch &&
        identical(session.api, api) &&
        session.connected;
    loading = true;
    error = null;
    notifyListeners();
    try {
      final result = await (api as RuntimeSettingsApi).runtimeSettings();
      if (!current()) return;
      instanceId = result['instance_id'] as String;
      settings = List.unmodifiable((result['settings'] as List)
          .map((entry) => freezeJson(Map<String, dynamic>.from(entry as Map))));
    } catch (_) {
      if (current()) {
        error = 'Runtime settings are unavailable. Refresh to retry.';
        settings = const [];
        instanceId = null;
      }
    } finally {
      if (current()) {
        loading = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    session.removeListener(_bind);
    super.dispose();
  }
}
