import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// A short-lived session lease; never starts voice during connection or recovery.
class VoiceController extends ChangeNotifier {
  VoiceController(this.session) {
    session.addListener(_bind);
    _bind();
  }
  final DesktopSession session;
  OdessyApi? _api;
  String? _sid;
  Timer? _lease;
  Object? _heartbeatTicket;
  int _epoch = 0;
  int _lastVoiceEvent = 0;
  bool _disposed = false, pending = false, uncertain = false;
  String state = 'off', error = '';
  bool inputAvailable = false, outputAvailable = false;
  bool get available => session.connected && _sid != null && _api is VoiceApi;
  bool _current(int epoch, OdessyApi api, String sid) =>
      !_disposed &&
      epoch == _epoch &&
      identical(api, session.api) &&
      session.connected &&
      session.session?.id == sid;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  void _bind() {
    final api = session.api,
        sid = session.connected ? session.session?.id : null;
    if (identical(api, _api) && sid == _sid) {
      _readVoiceErrors();
      return;
    }
    final old = _api, oldSid = _sid;
    final wasActive = state != 'off' || pending || uncertain;
    _lease?.cancel();
    _heartbeatTicket = null;
    if (wasActive && old is VoiceApi && oldSid != null) {
      unawaited((old as VoiceApi)
          .stopVoice(oldSid)
          .catchError((Object _) => <String, dynamic>{}));
    }
    ++_epoch;
    _api = api;
    _sid = sid;
    _lastVoiceEvent = session.lastEvent;
    pending = false;
    uncertain = false;
    state = 'off';
    error = '';
    inputAvailable = outputAvailable = false;
    _notify();
  }

  void _readVoiceErrors() {
    final events = session.session?.events ?? const <SessionEvent>[];
    var changed = false;
    for (final event in events) {
      if (event.id <= _lastVoiceEvent) continue;
      _lastVoiceEvent = event.id;
      if (event.type != 'voice_error' || (!pending && state == 'off')) continue;
      error = switch (event.data['stage']) {
        'capture' =>
          'Microphone capture failed. Check the input device and local audio setup.',
        'wake' =>
          'Wake detection failed. Check the local wake model and voice assets.',
        'stt' =>
          'Speech recognition failed. Check the local recognition setup and voice assets.',
        'synthesis' =>
          'Speech synthesis failed. Check the local voice output assets.',
        'playback' =>
          'Voice playback failed. Check the output device and local audio setup.',
        _ => 'Voice audio failed. Check the local audio and voice setup.',
      };
      changed = true;
    }
    if (changed) _notify();
  }

  void _apply(Json response) {
    state = response['state'] as String;
    inputAvailable = response['input_available'] as bool;
    outputAvailable = response['output_available'] as bool;
    if (state == 'off' || state == 'unavailable') _lease?.cancel();
  }

  Future<void> start() async {
    if (!available ||
        pending ||
        uncertain ||
        state != 'off' ||
        session.busy ||
        session.session?.running == true) {
      return;
    }
    final api = _api!, sid = _sid!, epoch = ++_epoch;
    _lastVoiceEvent = session.lastEvent;
    pending = true;
    error = '';
    _notify();
    try {
      final response = await (api as VoiceApi).startVoice(sid);
      if (!_current(epoch, api, sid)) return;
      _apply(response);
      if (state == 'starting' || state == 'active') {
        _lease?.cancel();
        _lease = Timer.periodic(
            const Duration(seconds: 5), (_) => unawaited(_heartbeat()));
      }
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        if (failure is ApiFailure &&
            const {400, 401, 404, 405, 409}.contains(failure.status)) {
          error = 'Voice could not start for this session.';
        } else {
          uncertain = true;
          error =
              'Voice start was not confirmed. Stop voice before trying again; the service lease also expires automatically.';
          try {
            await (api as VoiceApi).stopVoice(sid);
            if (_current(epoch, api, sid)) uncertain = false;
          } catch (_) {}
        }
      }
    } finally {
      if (_current(epoch, api, sid)) {
        pending = false;
        _notify();
      }
    }
  }

  Future<void> _heartbeat() async {
    if (!available || pending || uncertain || _heartbeatTicket != null) return;
    final api = _api!, sid = _sid!, epoch = _epoch;
    final ticket = _heartbeatTicket = Object();
    try {
      final response = await (api as VoiceApi).heartbeatVoice(sid);
      if (!_current(epoch, api, sid)) return;
      _apply(response);
      _notify();
    } catch (_) {
      if (!_current(epoch, api, sid)) return;
      _lease?.cancel();
      try {
        final response = await (api as VoiceApi).voiceStatus(sid);
        if (!_current(epoch, api, sid)) return;
        _apply(response);
        if (state == 'unavailable') {
          if (error.isEmpty) {
            error =
                'Voice input is unavailable. Check local audio and voice assets.';
          }
        } else if (state == 'stop_failed') {
          uncertain = true;
          error =
              'Voice shutdown failed. Restart the local service to release audio.';
        } else if (state != 'off') {
          uncertain = true;
          error = 'Voice status is uncertain. Stop voice before continuing.';
        }
      } catch (_) {
        if (_current(epoch, api, sid)) {
          uncertain = true;
          error = 'Voice status is uncertain. The service lease will expire.';
        }
      }
      _notify();
    } finally {
      if (identical(_heartbeatTicket, ticket)) _heartbeatTicket = null;
    }
  }

  Future<void> stop() async {
    if (!available || pending) return;
    final api = _api!, sid = _sid!, epoch = ++_epoch;
    _heartbeatTicket = null;
    pending = true;
    _lease?.cancel();
    _notify();
    try {
      final response = await (api as VoiceApi).stopVoice(sid);
      if (!_current(epoch, api, sid)) return;
      _apply(response);
      uncertain = false;
      error = '';
    } catch (_) {
      if (_current(epoch, api, sid)) {
        uncertain = true;
        error = 'Voice stop was not confirmed. The service lease will expire.';
      }
    } finally {
      if (_current(epoch, api, sid)) {
        pending = false;
        _notify();
      }
    }
  }

  @override
  void dispose() {
    session.removeListener(_bind);
    _lease?.cancel();
    final api = _api, sid = _sid;
    if ((state != 'off' || pending || uncertain) &&
        api is VoiceApi &&
        sid != null) {
      unawaited((api as VoiceApi)
          .stopVoice(sid)
          .catchError((Object _) => <String, dynamic>{}));
    }
    _disposed = true;
    ++_epoch;
    super.dispose();
  }
}
