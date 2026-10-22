import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Owns transport identity and admission. Presentation never constructs requests.
class DesktopSession extends ChangeNotifier {
  DesktopSession(this.api);
  OdessyApi api;
  Session? session;
  List<String> profiles = [];
  String status = 'Connecting', error = '', route = '';
  bool connected = false, busy = false, stopping = false, _disposed = false;
  int _epoch = 0;
  StreamSubscription<SessionEvent>? _subscription;
  Timer? _retry;
  int get lastEvent =>
      session?.events.fold<int>(0, (n, e) => e.id > n ? e.id : n) ?? 0;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> start() async {
    final epoch = ++_epoch;
    busy = true;
    error = '';
    _notify();
    try {
      final values = await api.profiles();
      final fresh = await api.createSession();
      if (_disposed || epoch != _epoch) return;
      profiles = values;
      session = fresh;
      connected = true;
      status = 'Ready';
      _listen(epoch);
    } catch (e) {
      error = _safeError(e);
      status = 'Offline';
      connected = false;
    } finally {
      busy = false;
      _notify();
    }
  }

  String _safeError(Object e) => e is ApiFailure
      ? e.toString()
      : 'Connection unavailable. Check that the local service is running.';
  void _listen(int epoch) {
    final sid = session!.id;
    _subscription = api.events(sid, lastEvent).listen((event) async {
      if (_disposed || epoch != _epoch) return;
      if (event.type == 'selection') {
        route = '${event.data['profile']} · ${event.data['reason'] ?? ''}';
      }
      if (event.type == 'stopped') {
        status = 'Stopped · completed actions remain completed';
      }
      if (event.type == 'error') {
        error = 'The turn failed. Inspect the local service diagnostics.';
      }
      await refresh(epoch: epoch);
    },
        onError: (Object e) => _disconnected(epoch, e),
        onDone: () => _disconnected(epoch, StateError('Closed')));
  }

  void _disconnected(int epoch, Object error) {
    if (_disposed || epoch != _epoch || _retry != null) return;
    connected = false;
    status = 'Reconnecting · no requests will be replayed';
    _notify();
    _retry = Timer(const Duration(seconds: 2), () async {
      if (_disposed || epoch != _epoch) return;
      try {
        await _subscription?.cancel();
        await refresh(epoch: epoch, propagate: true);
        if (_disposed || epoch != _epoch) return;
        connected = true;
        status = 'Reconnected';
        _retry = null;
        _listen(epoch);
        _notify();
      } catch (e) {
        _retry = null;
        _disconnected(epoch, e);
      }
    });
  }

  Future<void> refresh({int? epoch, bool propagate = false}) async {
    final captured = epoch ?? _epoch;
    final sid = session?.id;
    if (sid == null) return;
    try {
      final value = await api.snapshot(sid);
      final version = value.events.fold<int>(0, (n, e) => e.id > n ? e.id : n);
      if (_disposed || captured != _epoch || version < lastEvent) return;
      session = value;
      if (value.running) {
        status = value.pending.isNotEmpty ? 'Waiting for you' : 'Working';
      } else if (!status.startsWith('Stopped')) {
        status = 'Ready';
      }
      _notify();
    } catch (e) {
      if (propagate) {
        rethrow;
      }
      error = _safeError(e);
      _notify();
    }
  }

  Future<bool> command(Future<void> Function(String sid) operation,
      {bool requireIdle = false}) async {
    if (busy ||
        !connected ||
        session == null ||
        (requireIdle && session!.running)) {
      return false;
    }
    busy = true;
    error = '';
    _notify();
    final epoch = _epoch;
    try {
      await operation(session!.id);
      await refresh(epoch: epoch);
      return true;
    } catch (e) {
      error = _safeError(e);
      await refresh(epoch: epoch);
      return false;
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<bool> send(String text) =>
      command((sid) => api.send(sid, text.trim()), requireIdle: true);
  Future<void> stop() async {
    if (stopping || !connected || session == null) return;
    final transport = api;
    final epoch = _epoch;
    final sid = session!.id;
    stopping = true;
    error = '';
    _notify();
    try {
      await transport.stop(sid);
      await refresh(epoch: epoch);
      if (!_disposed && epoch == _epoch) {
        status = 'Stopped · completed actions remain completed';
      }
    } catch (e) {
      if (!_disposed && epoch == _epoch) {
        error = 'Stop was not confirmed. ${_safeError(e)}';
        await refresh(epoch: epoch);
      }
    } finally {
      stopping = false;
      _notify();
    }
  }

  Future<void> select(String profile) async {
    await command((sid) => api.select(sid, profile));
  }

  Future<Json> loadSettings() => api.settings();
  Future<void> updateConsultationLimit(int value) async {
    if (value < 0 || value > 32) throw ArgumentError('Limit must be 0–32');
    final transport = api;
    final epoch = _epoch;
    final latest = await transport.settings();
    if (_disposed || epoch != _epoch || !identical(transport, api)) {
      throw StateError('Connection changed; settings were not saved.');
    }
    await transport.saveSettings({
      ...latest,
      'settings': {...latest['settings'] as Map, 'max_consultations': value}
    });
  }

  Future<void> resolve(Json pending, {bool? approved, String? answer}) async {
    final id = pending['id'] as String;
    if (!(session?.pending.any((e) => e['id'] == id) ?? false)) {
      error = 'This request is no longer pending.';
      _notify();
      return;
    }
    await command((sid) => approved != null
        ? api.approve(sid, id, approved)
        : api.answer(sid, id, answer!));
  }

  Future<void> replace(OdessyApi next) async {
    if (busy || stopping || session?.running == true) {
      next.close();
      error = 'Stop the active turn before changing connection.';
      _notify();
      return;
    }
    ++_epoch;
    _retry?.cancel();
    _retry = null;
    await _subscription?.cancel();
    final previous = api;
    final sid = session?.id;
    if (sid != null) {
      try {
        await previous.deleteSession(sid);
      } catch (_) {/* Closing the client never retries work. */}
    }
    previous.close();
    api = next;
    session = null;
    route = '';
    connected = false;
    await start();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    _retry?.cancel();
    unawaited(_subscription?.cancel());
    final transport = api, sid = session?.id;
    unawaited(() async {
      try {
        if (sid != null) await transport.deleteSession(sid);
      } catch (_) {
        /* Shutdown does not replay or resubmit anything. */
      } finally {
        transport.close();
      }
    }());
    super.dispose();
  }
}
