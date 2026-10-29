import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Owns pairing-operation admission and stale-response rejection. Native Rust,
/// not this controller or a model, decides authentication and trust transitions.
class PairingController extends ChangeNotifier {
  PairingController(this.backend);
  final PairingBackend backend;
  PairingSnapshot? snapshot;
  bool busy = false, _disposed = false;
  int _epoch = 0;
  String error = '', message = '';
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> _run(Future<PairingSnapshot> Function() operation) async {
    if (busy || _disposed) return;
    final epoch = ++_epoch;
    busy = true;
    error = '';
    message = '';
    _notify();
    try {
      final value = await operation();
      if (!_disposed && epoch == _epoch) snapshot = value;
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        error =
            'Pairing could not complete. Inspect status before retrying; completed changes are not automatically undone.';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        _notify();
      }
    }
  }

  Future<void> load() => _run(backend.status);

  /// Refresh while a window is open without claiming the busy state or cancelling
  /// a user-initiated operation. Used to watch for the other computer's approval.
  Future<void> refresh() async {
    if (busy || _disposed) return;
    try {
      final value = await backend.status();
      if (!_disposed && !busy) {
        snapshot = value;
        _notify();
      }
    } catch (_) {
      // A failed poll leaves the last known state; it is not progress and must not
      // be reported as one.
    }
  }
  Future<void> join(String address, String code) =>
      _run(() => backend.join(address, code));
  Future<void> confirm() async {
    final state = snapshot;
    if (state == null || state.reviewToken.isEmpty) return;
    await _run(() => backend.confirm(state.sessionId, state.reviewToken));
  }

  Future<void> cancel() async {
    if (_disposed) return;
    ++_epoch;
    message = '';
    error = '';
    try {
      await backend.cancel();
      if (!_disposed) {
        message =
            'Cancellation requested. Inspect status; completed changes remain completed.';
      }
    } catch (_) {
      if (!_disposed) {
        error =
            'Cancellation outcome unknown. Inspect status before retrying; completed changes may remain.';
      }
    } finally {
      if (!_disposed) {
        busy = false;
        _notify();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    unawaited(backend.close());
    super.dispose();
  }
}
