import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// Binds every result to the session and transport that requested it.
class JobsController extends ChangeNotifier {
  JobsController(this.session) {
    session.addListener(_changed);
    _changed();
  }
  final DesktopSession session;
  OdessyApi? _api;
  String? _sid;
  int _epoch = 0;
  bool _disposed = false,
      loading = false,
      submitting = false,
      cancelling = false;
  bool submissionUncertain = false;
  String? error, nextCursor;
  List<Json> jobs = const [];
  Json? selected;
  final Set<String> _uncertainCancellations = {};
  String _cancelKey(String sid, String id) => '$sid:$id';
  bool get selectedCancellationUncertain =>
      _sid != null &&
      selected?['job_id'] is String &&
      _uncertainCancellations
          .contains(_cancelKey(_sid!, selected!['job_id'] as String));

  bool get available => session.connected && _sid != null && _api is JobsApi;
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
    if (identical(api, _api) && sid == _sid) return;
    if (submitting) submissionUncertain = true;
    ++_epoch;
    _api = api;
    _sid = sid;
    jobs = const [];
    selected = null;
    nextCursor = null;
    error = null;
    loading = submitting = cancelling = false;
    _notify();
    if (available) unawaited(refresh());
  }

  Future<void> refresh({bool more = false}) async {
    if (!available || loading || (more && nextCursor == null)) return;
    final api = _api!, sid = _sid!, epoch = _epoch;
    final cursor = more ? nextCursor : null;
    loading = true;
    error = null;
    _notify();
    try {
      final page = await (api as JobsApi).listJobs(sid, after: cursor);
      if (!_current(epoch, api, sid)) return;
      final incoming = (page['jobs'] as List)
          .map((item) => freezeJson(Map<String, dynamic>.from(item as Map)))
          .toList();
      final ids = <String>{};
      final combined = <Json>[];
      for (final item in [if (more) ...jobs, ...incoming]) {
        if (!ids.add(item['job_id'] as String)) {
          throw const FormatException('Duplicate job');
        }
        combined.add(item);
      }
      final next = page['next_cursor'] as String?;
      if (next != null && (next == cursor || !ids.contains(next))) {
        throw const FormatException('Invalid job cursor');
      }
      jobs = List.unmodifiable(combined);
      nextCursor = next;
    } catch (_) {
      if (_current(epoch, api, sid)) {
        error = 'Could not load jobs. Refresh to retry.';
      }
    } finally {
      if (_current(epoch, api, sid)) {
        loading = false;
        _notify();
      }
    }
  }

  Future<void> inspect(String id) async {
    if (!available || loading || !jobs.any((job) => job['job_id'] == id)) {
      return;
    }
    final api = _api!, sid = _sid!, epoch = _epoch;
    loading = true;
    error = null;
    _notify();
    try {
      final detail = await (api as JobsApi).readJob(sid, id);
      if (!_current(epoch, api, sid)) return;
      if (detail['job_id'] != id) throw const FormatException('Wrong job');
      selected = freezeJson(detail);
      _uncertainCancellations.remove(_cancelKey(sid, id));
    } catch (_) {
      if (_current(epoch, api, sid)) error = 'Could not load this job.';
    } finally {
      if (_current(epoch, api, sid)) {
        loading = false;
        _notify();
      }
    }
  }

  Future<void> submit(
      {required String participant,
      required String task,
      required String mode}) async {
    if (!available ||
        submitting ||
        submissionUncertain ||
        participant.trim().isEmpty ||
        task.trim().isEmpty ||
        !const {'advice', 'patch'}.contains(mode)) {
      return;
    }
    final api = _api!, sid = _sid!, epoch = _epoch;
    submitting = true;
    error = null;
    _notify();
    try {
      await (api as JobsApi).submitJob(sid, {
        'participant': participant.trim(),
        'task': task.trim(),
        'mode': mode
      });
      if (_current(epoch, api, sid)) await refresh();
    } catch (failure) {
      if (_current(epoch, api, sid)) {
        if (failure is ApiFailure && failure.status == 403) {
          error = 'The service declined this job.';
        } else {
          submissionUncertain = true;
          error =
              'Submission was not confirmed. Check session activity before trying again.';
        }
      }
    } finally {
      if (_current(epoch, api, sid)) {
        submitting = false;
        _notify();
      }
    }
  }

  void acknowledgeUncertainSubmission() {
    if (submitting) return;
    submissionUncertain = false;
    error = null;
    _notify();
  }

  Future<void> cancel(String id) async {
    if (!available ||
        loading ||
        cancelling ||
        selectedCancellationUncertain ||
        selected?['job_id'] != id ||
        !const {'queued', 'running'}.contains(selected?['state'])) {
      return;
    }
    final api = _api!, sid = _sid!, epoch = _epoch;
    _uncertainCancellations.add(_cancelKey(sid, id));
    cancelling = true;
    error = null;
    _notify();
    try {
      final result = await (api as JobsApi).cancelJob(sid, id);
      if (!_current(epoch, api, sid)) return;
      if (result['job_id'] != id) throw const FormatException('Wrong job');
      selected = freezeJson(result);
      _uncertainCancellations.remove(_cancelKey(sid, id));
      await refresh();
    } catch (_) {
      if (_current(epoch, api, sid)) {
        error =
            'Cancellation was not confirmed. Reload this job before retrying.';
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
