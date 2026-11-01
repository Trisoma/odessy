import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

class ExecutionHostsController extends ChangeNotifier {
  ExecutionHostsController(this.api, {this.onConnected});
  final ExecutionHostsApi? api;
  final Future<void> Function()? onConnected;
  List<Json> hosts = [];
  Json? connection;
  String state = 'disconnected', message = '';
  bool busy = false, _disposed = false;
  Timer? _poll;
  void _notify() { if (!_disposed) notifyListeners(); }

  Future<void> scan() async {
    if (api == null || busy || _disposed) return;
    busy = true;
    message = 'Finding nearby execution hosts…';
    _notify();
    try {
      final result = await api!.scanExecutionHosts();
      if (_disposed) return;
      hosts = (result['hosts'] as List).whereType<Map>().map((v) => Map<String, dynamic>.from(v)).toList();
      connection = result['connection'] is Map ? Map<String, dynamic>.from(result['connection'] as Map) : null;
      message = hosts.isEmpty ? 'No execution hosts found. Open OdessyExecutor on the same Wi-Fi or LAN.' : '';
    } catch (_) {
      message = 'Could not find nearby hosts. Check the local coordinator, Wi-Fi and discovery dependencies.';
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<void> connect(String id) async {
    if (api == null || busy || _disposed) return;
    busy = true;
    state = 'waiting_for_approval';
    message = 'Waiting for approval. On the execution host, type Y in its terminal.';
    _notify();
    _poll = Timer.periodic(const Duration(seconds: 1), (_) async {
      try {
        final status = await api!.executionHostStatus();
        if (!_disposed && busy && status['state'] == 'connecting') {
          state = 'connecting';
          message = 'Accepted. Connecting to tools…';
          _notify();
        }
      } catch (_) { /* The final request reports a connection failure. */ }
    });
    try {
      final result = await api!.connectExecutionHost(id);
      if (_disposed) return;
      connection = Map<String, dynamic>.from(result['host'] as Map);
      state = 'connected';
      message = 'Connected · ${result['tools']} tools available';
      try {
        await onConnected?.call();
      } catch (_) {
        message = 'Tools connected. The model session could not reopen; check model settings. Execution configuration is retained.';
      }
    } catch (error) {
      state = 'failed';
      message = error is ExecutionConnectionFailure ? error.message : 'Connection failed. Check the execution host and try again. Chat remains available.';
    } finally {
      _poll?.cancel();
      _poll = null;
      busy = false;
      _notify();
    }
  }

  Future<void> disconnect() async {
    if (api == null || busy || _disposed) return;
    busy = true;
    _notify();
    try {
      await api!.disconnectExecutionHost();
      connection = null;
      state = 'disconnected';
      message = 'Disconnected. Text conversation remains available.';
      await onConnected?.call();
    } catch (_) {
      message = 'Could not disconnect. Stop any active turn and retry.';
    } finally {
      busy = false;
      _notify();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    super.dispose();
  }
}
