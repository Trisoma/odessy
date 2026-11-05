import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// Two-step handoff runs for the connected session.
///
/// Runs reach the panel two ways: `run` session events, which the service sends
/// on every change, and the snapshot a request returns. Every result is bound to
/// the transport and session that asked for it, like jobs. Handing over always
/// names the hash of the output this controller is showing; the service checks
/// it again, so a newer output can never be approved by mistake.
class HandoffController extends ChangeNotifier {
  HandoffController(this.session) {
    session.addListener(_changed);
    _changed();
  }
  final DesktopSession session;
  OdessyApi? _api;
  String? _sid;
  int _epoch = 0, _seenEvent = 0;
  bool _disposed = false;
  bool loading = false, starting = false, continuing = false;
  bool cancelling = false, loadingArtifact = false;

  /// A start whose outcome is unknown. Repeating it could start a second run,
  /// so it stays blocked until acknowledged.
  bool startUncertain = false;
  String? error, selectedId;

  /// The run whose handoff outcome is unknown; reloading it clears this.
  String? _uncertainHandoff;
  bool get continueUncertain =>
      selectedId != null && _uncertainHandoff == selectedId;

  /// The full output, kept only while it still matches the offered hash.
  Json? artifact;
  final Map<String, Json> _runs = {};
  final Map<String, int> _eventFor = {};
  final List<String> _order = [];

  bool get available =>
      session.connected && _sid != null && _api is HandoffRunsApi;

  /// Newest first.
  List<Json> get runs =>
      List.unmodifiable(_order.reversed.map((id) => _runs[id]!));
  Json? get selected => selectedId == null ? null : _runs[selectedId];

  /// The output waiting for a decision, or null when nothing is waiting.
  Json? get offered {
    final run = selected;
    if (run == null || run['state'] != 'awaiting_handoff') return null;
    final transfer = (run['transfers'] as List).first as Map;
    if (transfer['state'] != 'pending') return null;
    return transfer['artifact'] as Json?;
  }

  bool _current(int epoch, OdessyApi api, String sid) =>
      !_disposed &&
      epoch == _epoch &&
      identical(api, session.api) &&
      session.connected &&
      session.session?.id == sid;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  void _changed() {
    final api = session.api,
        sid = session.connected ? session.session?.id : null;
    final reset = !identical(api, _api) || sid != _sid;
    if (reset) {
      if (starting) startUncertain = true;
      // A run belongs to its session: the new one cannot act on it.
      _uncertainHandoff = null;
      ++_epoch;
      _api = api;
      _sid = sid;
      _seenEvent = 0;
      _runs.clear();
      _eventFor.clear();
      _order.clear();
      selectedId = null;
      artifact = null;
      error = null;
      loading = starting = continuing = cancelling = loadingArtifact = false;
    }
    if (_absorbEvents() || reset) _notify();
  }

  /// Reads `run` events newer than the last one seen. Returns whether any did.
  bool _absorbEvents() {
    final events = session.connected ? session.session?.events : null;
    if (events == null) return false;
    var changed = false;
    for (final event in events) {
      if (event.id <= _seenEvent) continue;
      _seenEvent = event.id;
      if (event.type != 'run') continue;
      final Json run;
      try {
        run = parseRunSnapshot(event.data);
      } on FormatException {
        continue; // A malformed event never replaces a drawable snapshot.
      }
      _eventFor[run['run_id'] as String] = event.id;
      _store(run);
      changed = true;
    }
    return changed;
  }

  void _store(Json run) {
    final id = run['run_id'] as String;
    if (!_runs.containsKey(id)) _order.add(id);
    _runs[id] = run;
    selectedId ??= id;
    final offered = this.offered;
    if (artifact != null &&
        (offered == null || offered['sha256'] != artifact!['sha256'])) {
      artifact = null;
    }
  }

  /// Applies a fetched snapshot unless an event for the same run arrived while
  /// the request was in flight: that event is at least as new.
  void _storeFetched(Json run, int eventBefore) {
    if ((_eventFor[run['run_id']] ?? 0) > eventBefore) return;
    _store(run);
  }

  void select(String id) {
    if (!_runs.containsKey(id) || id == selectedId) return;
    selectedId = id;
    artifact = null;
    error = null;
    _notify();
  }

  String _failure(Object failure, String fallback) {
    if (failure is ApiFailure) {
      return switch (failure.status) {
        403 => 'The service declined this request.',
        404 => 'The service no longer has this run. Runs end when it restarts.',
        409 => 'The run changed. Reload it before deciding.',
        400 => 'The service rejected this request. Check the participants, '
            'tasks and run state.',
        503 => 'Handoff runs are not configured for this session.',
        _ => fallback,
      };
    }
    return fallback;
  }

  Future<void> reload() async {
    final id = selectedId;
    if (!available || loading || id == null) return;
    final api = _api!, sid = _sid!, epoch = _epoch, before = _seenEvent;
    loading = true;
    error = null;
    _notify();
    try {
      final run = await (api as HandoffRunsApi).readRun(sid, id);
      if (!_current(epoch, api, sid)) return;
      if (run['run_id'] != id) throw const FormatException('Wrong run');
      _storeFetched(run, before);
      if (_uncertainHandoff == id) _uncertainHandoff = null;
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        error = _failure(failure, 'Could not load this run.');
      }
    } finally {
      if (_current(epoch, api, sid)) {
        loading = false;
        _notify();
      }
    }
  }

  Future<void> start({
    required String flow,
    required String fromParticipant,
    required String fromTask,
    required String toParticipant,
    required String toTask,
  }) async {
    final values = [fromParticipant, fromTask, toParticipant, toTask]
        .map((value) => value.trim())
        .toList();
    if (!available ||
        starting ||
        startUncertain ||
        !handoffFlows.contains(flow) ||
        values.any((value) => value.isEmpty)) {
      return;
    }
    final api = _api!, sid = _sid!, epoch = _epoch, before = _seenEvent;
    starting = true;
    error = null;
    _notify();
    try {
      final run = await (api as HandoffRunsApi).startRun(sid, {
        'flow': flow,
        'from_participant': values[0],
        'from_task': values[1],
        'to_participant': values[2],
        'to_task': values[3],
      });
      if (!_current(epoch, api, sid)) return;
      _storeFetched(run, before);
      selectedId = run['run_id'] as String;
      artifact = null;
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        if (failure is ApiFailure &&
            const {400, 403, 503}.contains(failure.status)) {
          error = _failure(failure, '');
        } else {
          startUncertain = true;
          error = 'The start was not confirmed. Check session activity '
              'before trying again.';
        }
      }
    } finally {
      if (_current(epoch, api, sid)) {
        starting = false;
        _notify();
      }
    }
  }

  void acknowledgeUncertainStart() {
    if (starting) return;
    startUncertain = false;
    error = null;
    _notify();
  }

  Future<void> loadArtifact() async {
    final id = selectedId, offer = offered;
    if (!available || loadingArtifact || id == null || offer == null) return;
    final api = _api!, sid = _sid!, epoch = _epoch;
    loadingArtifact = true;
    error = null;
    _notify();
    try {
      final full = await (api as HandoffRunsApi).readRunArtifact(sid, id);
      if (!_current(epoch, api, sid) || selectedId != id) return;
      // Shown only while it is the output that is still on offer.
      if (full['sha256'] != offered?['sha256']) {
        throw const FormatException('Output changed');
      }
      artifact = full;
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        error = failure is FormatException
            ? 'The output changed while loading. Reload the run.'
            : _failure(failure, 'Could not load the full output.');
      }
    } finally {
      if (_current(epoch, api, sid)) {
        loadingArtifact = false;
        _notify();
      }
    }
  }

  /// Hands the displayed output to the second agent, after service approval.
  Future<void> handOver() async {
    final id = selectedId, offer = offered;
    if (!available ||
        continuing ||
        continueUncertain ||
        id == null ||
        offer == null) {
      return;
    }
    final sha = offer['sha256'] as String;
    final api = _api!, sid = _sid!, epoch = _epoch, before = _seenEvent;
    continuing = true;
    error = null;
    _notify();
    try {
      final run = await (api as HandoffRunsApi).continueRun(sid, id, sha);
      if (!_current(epoch, api, sid)) return;
      if (run['run_id'] != id) throw const FormatException('Wrong run');
      _storeFetched(run, before);
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        if (failure is ApiFailure &&
            const {400, 403, 404}.contains(failure.status)) {
          // A refusal, a decline, or a run that changed: nothing was handed over
          // by this request. The run's events show what happened.
          error = _failure(failure, '');
        } else {
          _uncertainHandoff = id;
          error = 'The handoff was not confirmed. Reload the run before '
              'deciding again.';
        }
      }
    } finally {
      if (_current(epoch, api, sid)) {
        continuing = false;
        _notify();
      }
    }
  }

  Future<void> cancel() async {
    final id = selectedId, run = selected;
    if (!available ||
        cancelling ||
        id == null ||
        run == null ||
        !activeRunStates.contains(run['state'])) {
      return;
    }
    final api = _api!, sid = _sid!, epoch = _epoch, before = _seenEvent;
    cancelling = true;
    error = null;
    _notify();
    try {
      final result = await (api as HandoffRunsApi).cancelRun(sid, id);
      if (!_current(epoch, api, sid)) return;
      if (result['run_id'] != id) throw const FormatException('Wrong run');
      _storeFetched(result, before);
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        error = _failure(failure,
            'Cancellation was not confirmed. Reload the run before retrying.');
      }
    } finally {
      if (_current(epoch, api, sid)) {
        cancelling = false;
        _notify();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    session.removeListener(_changed);
    super.dispose();
  }
}
