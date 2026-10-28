import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// Bound to the existing API/session. No recorder, provider or permissions in Dart.
class VoiceController extends ChangeNotifier {
  VoiceController(this.session) {
    session.addListener(_changed);
    _changed();
  }
  final DesktopSession session;
  OdessyApi? _api;
  String? _sid;
  bool _disposed = false;
  int _epoch = 0;
  int _operation = 0, _event = 0;
  VoiceSnapshot? snapshot;
  String error = '';
  bool busy = false;
  bool get recording => snapshot?.state == 'recording';
  bool get supported => snapshot?.supported == true && session.connected;
  void _changed() {
    final sid = session.session?.id;
    if (!identical(_api, session.api) || _sid != sid) {
      _api = session.api;
      _sid = sid;
      _epoch++;
      snapshot = null;
      error = '';
      busy = false;
      _event = 0;
      if (sid != null) refresh();
    }
    for (final event in (session.session?.events ?? <SessionEvent>[])
        .where((e) => e.id > _event)) {
      _event = event.id;
      if (event.type == 'voice_state') {
        try {
          snapshot = VoiceSnapshot.fromJson(event.data);
          if (!_disposed) notifyListeners();
        } catch (_) {/* Unknown event remains in activity. */}
      }
    }
  }

  Future<void> refresh() async {
    final api = _api, sid = _sid, epoch = _epoch;
    if (api is! DesktopVoiceApi || sid == null) return;
    try {
      final value = await (api as DesktopVoiceApi).voiceState(sid);
      if (!_disposed && epoch == _epoch) {
        snapshot = value;
        notifyListeners();
      }
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        error = 'Desktop audio unavailable. Text remains available.';
        notifyListeners();
      }
    }
  }

  Future<void> command(String action) async {
    if (action == 'cancel_capture' && snapshot?.mode == 'wake') {
      action = 'disable_wake';
    }
    final api = _api, sid = _sid, epoch = _epoch;
    if (api is! DesktopVoiceApi ||
        sid == null ||
        (busy &&
            action != 'cancel_capture' &&
            action != 'stop_playback' &&
            action != 'disable_wake')) {
      return;
    }
    final operation = ++_operation;
    busy = true;
    error = '';
    notifyListeners();
    final pending = session.session?.pending ?? [];
    final interaction =
        pending.length == 1 ? pending.first['id'] as String? : null;
    try {
      final value = await (api as DesktopVoiceApi).voiceCommand(sid, action,
          captureId: snapshot?.captureId, interactionId: interaction);
      if (!_disposed && epoch == _epoch && operation == _operation) {
        snapshot = value;
      }
    } catch (_) {
      if (!_disposed && epoch == _epoch && operation == _operation) {
        error =
            'Audio action failed. Check voice settings or answer in text; no model request was retried.';
      }
    } finally {
      if (!_disposed && epoch == _epoch && operation == _operation) {
        busy = false;
        notifyListeners();
        await refresh();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    session.removeListener(_changed);
    super.dispose();
  }
}
