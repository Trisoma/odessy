import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

import 'setup/disconnected_api.dart';

/// Owns transport identity and admission. Presentation never constructs requests.
class DesktopSession extends ChangeNotifier {
  DesktopSession(this.api);
  OdessyApi api;
  Session? session;
  List<String> profiles = [];
  Map<String, Profile> profileDetails = {};
  String profileLabel(String name) {
    final detail = profileDetails[name];
    final model = detail?.model;
    if (model == null || model.isEmpty || model == name) return name;
    final provider = detail?.provider;
    return "$model${provider == null ? '' : ' · $provider'} ($name)";
  }

  String status = 'Connecting', error = '', route = '';
  bool connected = false, busy = false, stopping = false, _disposed = false;

  int _epoch = 0;
  Future<void>? _shutdown;
  Json? _consultationVersion;
  OdessyApi? _consultationTransport;
  StreamSubscription<SessionEvent>? _subscription;
  Timer? _retry;
  int get lastEvent =>
      session?.events.fold<int>(0, (n, e) => e.id > n ? e.id : n) ?? 0;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> start() async {
    if (_disposed || busy) return;
    if (api is DisconnectedApi) {
      error = '';
      connected = false;
      status = 'Not connected';
      _notify();
      return;
    }
    final epoch = ++_epoch;
    busy = true;
    error = '';
    _notify();
    // A laptop may have no model and no session yet, but its user still has to
    // answer a request. The pairing stream is therefore opened first, and its
    // failure never blocks the session.

    try {
      final legacyProfiles = await api.profiles();
      final details = api is ProfileMetadataApi
          ? await (api as ProfileMetadataApi).profileMetadata()
          : <Profile>[];
      final fresh = await api.createSession();
      final values = fresh.availableProfiles ?? legacyProfiles;
      if (_disposed || epoch != _epoch) return;
      profiles = values;
      profileDetails = {for (final detail in details) detail.name: detail};
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

  Future<void> reloadExecutionTarget() async {
    if (busy || session?.running == true) {
      throw StateError('Stop the active turn first.');
    }
    await _subscription?.cancel();
    _subscription = null;
    final old = session;
    if (old != null) await api.deleteSession(old.id);
    session = null;
    await start();
  }

  String _safeError(Object e) => e is ApiFailure
      ? e.toString()
      : 'Connection unavailable. Check that the local service is running.';

  void _listen(int epoch) {
    final sid = session!.id;
    _subscription = api
        .events(sid, lastEvent)
        .listen(
          (event) async {
            if (_disposed || epoch != _epoch) return;
            if (event.type == 'selection') {
              route =
                  '${event.data['profile']} · ${event.data['reason'] ?? ''}';
            }
            if (event.type == 'stopped') {
              status = 'Stopped · completed actions remain completed';
            }
            if (event.type == 'error') {
              final detail = event.data['message'];
              final text = detail is String
                  ? detail.replaceAll(RegExp(r'[\x00-\x1f\x7f]'), ' ').trim()
                  : '';
              error = text.isEmpty
                  ? 'The turn failed without an error detail.'
                  : text.substring(0, text.length > 600 ? 600 : text.length);
              status = 'Failed';
            }
            await refresh(epoch: epoch);
            if (event.type == 'error' && epoch == _epoch && !_disposed) {
              status = 'Failed';
              _notify();
            }
          },
          onError: (Object e) => _disconnected(epoch, e),
          onDone: () => _disconnected(epoch, StateError('Closed')),
        );
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
      if (value.id != sid) throw StateError('Session identity changed');
      session = reduceSessionSnapshot(session, value);
      if (session!.availableProfiles != null) {
        profiles = session!.availableProfiles!;
      }
      if (value.running) {
        status = value.pending.isNotEmpty ? 'Waiting for you' : 'Working';
      } else if (!status.startsWith('Stopped')) {
        status = error.isEmpty ? 'Ready' : 'Failed';
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

  Future<bool> command(
    Future<void> Function(String sid) operation, {
    bool requireIdle = false,
  }) async {
    if (_disposed ||
        busy ||
        stopping ||
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

  Future<Json> loadSettings() async {
    final transport = api;
    if (transport.isDemo) return transport.settings();
    _consultationVersion = null;
    if (transport is! ConsultationLimitApi) {
      throw StateError('Safe settings updates are unavailable.');
    }
    final epoch = _epoch;
    final result = await (transport as ConsultationLimitApi)
        .readConsultationLimit();
    if (_disposed || epoch != _epoch || !identical(transport, api)) {
      throw StateError('Connection changed.');
    }
    _consultationVersion = result;
    _consultationTransport = transport;
    return {
      'settings': {'max_consultations': result['max_consultations']},
    };
  }

  Future<void> updateConsultationLimit(int value) async {
    if (value < 0 || value > 32) throw ArgumentError('Limit must be 0–32');
    final transport = api;
    final epoch = _epoch;
    if (!transport.isDemo) {
      final version = _consultationVersion;
      _consultationVersion =
          null; // A lost response must not be retried blindly.
      if (transport is! ConsultationLimitApi ||
          version == null ||
          !identical(_consultationTransport, transport)) {
        throw StateError('Reload settings before saving.');
      }
      await (transport as ConsultationLimitApi).saveConsultationLimit({
        'instance_id': version['instance_id'],
        'revision': version['revision'],
        'max_consultations': value,
      });
      if (_disposed || epoch != _epoch || !identical(transport, api)) {
        throw StateError('Connection changed; save result is uncertain.');
      }
      return;
    }
    final latest = await transport.settings();
    if (_disposed || epoch != _epoch || !identical(transport, api)) {
      throw StateError('Connection changed; settings were not saved.');
    }
    await transport.saveSettings({
      ...latest,
      'settings': {...latest['settings'] as Map, 'max_consultations': value},
    });
  }

  Future<void> resolve(Json pending, {bool? approved, String? answer}) async {
    final id = pending['id'];
    if (id is! String ||
        id.isEmpty ||
        (approved == null && (answer == null || answer.trim().isEmpty)) ||
        (approved != null && answer != null)) {
      error = 'Invalid interaction response.';
      _notify();
      return;
    }
    final current = session?.pending.where((e) => identical(e, pending));
    final expiry = pending['expires_at'];
    final channels = pending['allowed_channels'];
    final kind = pending['kind'];
    if (current == null ||
        current.isEmpty ||
        expiry is! num ||
        expiry <= DateTime.now().millisecondsSinceEpoch / 1000 ||
        channels is! List ||
        !channels.contains('authenticated_ui') ||
        (approved != null && kind != 'approval') ||
        (answer != null && kind != 'question') ||
        (answer != null &&
            pending['allow_free_text'] != true &&
            !(pending['choices'] is List &&
                (pending['choices'] as List).contains(answer)))) {
      error = 'This request is no longer pending.';
      _notify();
      return;
    }
    await command(
      (sid) => approved != null
          ? api.approve(sid, id, approved)
          : api.answer(sid, id, answer!),
    );
  }

  Future<void> replace(OdessyApi next) async {
    if (_disposed || busy || stopping || session?.running == true) {
      next.close();
      error = 'Stop the active turn before changing connection.';
      _notify();
      return;
    }
    final transition = ++_epoch;
    busy = true;
    connected = false;
    _retry?.cancel();
    _retry = null;
    await _subscription?.cancel();
    final previous = api;
    final sid = session?.id;
    if (sid != null) {
      try {
        await previous.deleteSession(sid);
      } catch (_) {
        /* Closing the client never retries work. */
      }
    }
    previous.close();
    if (_disposed || transition != _epoch) {
      next.close();
      return;
    }
    api = next;
    session = null;
    route = '';
    connected = false;
    busy = false;
    await start();
  }

  /// Explicitly end this idle session and load a new configuration snapshot.
  Future<void> newSession() async {
    if (_disposed ||
        busy ||
        stopping ||
        !connected ||
        session == null ||
        session!.running) {
      return;
    }
    final transport = api, sid = session!.id, epoch = ++_epoch;
    busy = true;
    connected = false;
    error = '';
    _retry?.cancel();
    _retry = null;
    _notify();
    try {
      await _subscription?.cancel();
      await transport.deleteSession(sid);
      if (_disposed || epoch != _epoch || !identical(api, transport)) return;
      session = null;
      profiles = [];
      route = '';
      busy = false;
      await start();
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        status = 'Offline';
        error = 'The previous session could not be closed. Reconnect or restart the app to continue.';
        _notify();
      }
    }
  }

  Future<void> shutdown() => _shutdown ??= _close();

  Future<void> _close() async {
    _disposed = true;
    connected = false;
    ++_epoch;
    _retry?.cancel();
    final transport = api, sid = session?.id;
    try {
      await _subscription?.cancel();
      if (sid != null) {
        await transport.deleteSession(sid).timeout(const Duration(seconds: 3));
      }
    } catch (_) {
      // Cleanup failure never replays work or prevents transport closure.
    } finally {
      transport.close();
    }
  }

  @override
  void dispose() {
    unawaited(shutdown());
    super.dispose();
  }
}
