import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// A transcript the coordinator already admitted for [sessionId].
///
/// Submission is the backend's: it emits this after admitting the spoken turn.
/// The composer therefore displays it and never sends it a second time.
@immutable
class VoiceTranscript {
  const VoiceTranscript(
      {required this.sessionId, required this.text, this.captureId});
  final String sessionId, text;
  final String? captureId;
}

/// Bound to the existing API/session. No recorder, provider or permissions in Dart.
///
/// Lifecycle rules enforced here:
/// * nothing is captured, armed or played on construction or on [refresh];
/// * every reply is bound to the api/sid/epoch that issued it, so a late answer
///   from a previous target cannot overwrite the current state;
/// * changing target or disposing releases the previous audio owner instead of
///   leaving an orphan capture or overlapping playback behind.
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
  Future<void>? _refreshing;
  VoiceSnapshot? snapshot;
  VoiceTranscript? transcript;
  String error = '';
  bool busy = false;

  bool get recording => snapshot?.state == 'recording';
  bool get transcribing => snapshot?.state == 'transcribing';
  bool get preparing => snapshot?.state == 'preparing';
  bool get cancelling => snapshot?.state == 'cancelling';
  bool get speaking => snapshot?.state == 'speaking';
  bool get wakeMode => snapshot?.mode == 'wake';
  bool get supported => snapshot?.supported == true && session.connected;
  String get state => snapshot?.state ?? 'unavailable';
  String get message => snapshot?.message ?? '';
  String? get captureId => snapshot?.captureId;
  bool get hasCapture => snapshot?.captureId != null;

  /// Offered only for idle, non-wake audio. A failed action may be retried.
  bool get canStartCapture =>
      supported &&
      !busy &&
      !wakeMode &&
      !hasCapture &&
      !transcribing &&
      !preparing &&
      !cancelling;
  bool get canFinishCapture => supported && !busy && recording && hasCapture;
  bool get canCancelCapture => supported && hasCapture;
  bool get canEnableWake => supported && !busy && !wakeMode;
  bool get canDisableWake => supported && wakeMode;
  bool get canTestInput =>
      supported && !busy && !wakeMode && !hasCapture && !transcribing;
  bool get canTestOutput => supported && !busy && !transcribing;

  void _changed() {
    final sid = session.session?.id;
    if (!identical(_api, session.api) || _sid != sid) {
      final previousApi = _api, previousSid = _sid, previous = snapshot;
      if (previousSid != null &&
          previousApi is DesktopVoiceApi &&
          previous != null &&
          _ownedAudio(previous)) {
        // The old owner may still be recording or playing. Release it on the
        // old transport; that call carries its own epoch and target guard.
        unawaited(
            _release(previousApi as DesktopVoiceApi, previousSid, previous));
      }
      _api = session.api;
      _sid = sid;
      _epoch++;
      snapshot = null;
      transcript = null;
      error = '';
      busy = false;
      _event = 0;
      _refreshing = null;
      if (sid != null) unawaited(refresh());
    }
    for (final event in (session.session?.events ?? <SessionEvent>[])
        .where((e) => e.id > _event)) {
      _event = event.id;
      if (event.type == 'voice_state') {
        try {
          snapshot = VoiceSnapshot.fromJson(event.data);
          if (!_disposed) notifyListeners();
        } catch (_) {/* Unknown event remains in activity. */}
      } else if (event.type == 'voice_transcript') {
        final text = event.data['text'], captured = _sid;
        if (captured != null && text is String && text.trim().isNotEmpty) {
          transcript = VoiceTranscript(
              sessionId: captured,
              text: text.trim(),
              captureId: event.data['capture_id'] as String?);
          if (!_disposed) notifyListeners();
        }
      }
    }
  }

  Future<void> refresh() async {
    final api = _api, sid = _sid, epoch = _epoch;
    if (api is! DesktopVoiceApi || sid == null) return;
    final pending = _refreshing;
    if (pending != null) return pending;
    final task = _refresh(api as DesktopVoiceApi, sid, epoch);
    _refreshing = task;
    try {
      await task;
    } finally {
      if (identical(_refreshing, task)) _refreshing = null;
    }
  }

  Future<void> _refresh(DesktopVoiceApi api, String sid, int epoch) async {
    try {
      final value = await api.voiceState(sid);
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
        unawaited(refresh());
      }
    }
  }

  /// Releases the audio owner this controller currently drives.
  ///
  /// For an explicit shell-level release (leaving the conversation) or a
  /// session change. It never starts capture and ignores failures: a revoked or
  /// closed target simply stays released.
  Future<void> release({bool stopPlayback = true}) async {
    final api = _api, sid = _sid, value = snapshot;
    if (api is! DesktopVoiceApi || sid == null || value == null) {
      _forgetLocal();
      return;
    }
    _forgetLocal();
    await _release(api as DesktopVoiceApi, sid, value,
        stopPlayback: stopPlayback);
  }

  void _forgetLocal() {
    _epoch++;
    snapshot = null;
    transcript = null;
    error = '';
    busy = false;
    _refreshing = null;
  }

  bool _ownedAudio(VoiceSnapshot value) =>
      value.mode == 'wake' ||
      value.captureId != null ||
      const {'recording', 'preparing', 'transcribing', 'speaking', 'listening'}
          .contains(value.state);

  /// Best effort, bounded to the target it was issued for.
  Future<void> _release(DesktopVoiceApi api, String sid, VoiceSnapshot value,
      {bool stopPlayback = true}) async {
    final epoch = _epoch;
    Future<void> send(String action, {String? capture}) async {
      try {
        await api.voiceCommand(sid, action, captureId: capture);
      } catch (_) {
        // Released or unreachable is the intended end state.
      }
    }

    final capture = value.captureId;
    if (capture != null) await send('cancel_capture', capture: capture);
    if (value.mode == 'wake') await send('disable_wake');
    if (stopPlayback && value.state == 'speaking') await send('stop_playback');
    // The released target's own stream is authoritative for its final state;
    // re-reading is only useful while this controller still owns it.
    if (!_disposed && epoch == _epoch && _sid == sid) unawaited(refresh());
  }

  @override
  void dispose() {
    if (_disposed) return;
    final api = _api, sid = _sid, value = snapshot;
    _disposed = true;
    _epoch++;
    session.removeListener(_changed);
    if (api is DesktopVoiceApi &&
        sid != null &&
        value != null &&
        _ownedAudio(value)) {
      unawaited(_release(api as DesktopVoiceApi, sid, value));
    }
    super.dispose();
  }
}
