import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// One packaged installation flow: verified context, exact review, journal-backed
/// progress and a single mutation. Late replies from a cancelled attempt are
/// discarded by generation rather than applied.
class PackageSetupController extends ChangeNotifier {
  PackageSetupController(this.backend, {SetupBackend? progressBackend})
      : progressBackend = progressBackend ?? backend;

  final SetupBackend backend;

  /// A second read-only helper, so progress can be read while installation runs.
  final SetupBackend progressBackend;

  Json context = const {}, review = const {}, result = const {};
  Json selection = const {
    'role': 'combined',
    'voice': {'enabled': false}
  };
  Json discovery = const {};
  String root = '', error = '', reason = '', status = '', stage = '';
  bool busy = false, interrupted = false, installed = false, discovering = false;
  int revision = 0, stepsDone = 0, stepsTotal = 0;

  Timer? _poll;
  bool _polling = false;
  int _epoch = 0;
  bool _disposed = false;

  double? get progress =>
      stepsTotal > 0 ? (stepsDone / stepsTotal).clamp(0.0, 1.0) : null;

  String get installationFormat =>
      (context['installation_format'] as String?) ?? 'new';

  bool get existingInstallation => installationFormat != 'new';

  String get planId => (review['plan_id'] as String?) ?? '';

  bool get canInstall =>
      !busy && !interrupted && planId.isNotEmpty && !existingInstallation;

  void edit({String? destination, Json? value}) {
    if (busy || _disposed) return;
    if (destination != null) root = destination.trim();
    if (value != null) selection = value;
    review = const {};
    stepsDone = 0;
    stepsTotal = 0;
    revision++;
    notifyListeners();
  }

  Future<Json> call(String action,
      {String? confirm, Json parameters = const {}}) async {
    final reply = await backend.execute(SetupRequest(
        id: 'package-${DateTime.now().microsecondsSinceEpoch}',
        operation: 'manage',
        selection: const {},
        parameters: {
          'action': action,
          'root': root,
          if (context['payload_root'] is String)
            'payload': context['payload_root'],
          'selection': selection,
          ...parameters,
          if (confirm != null) 'confirm': confirm,
        }));
    if (!reply.ok) {
      throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
    }
    return reply.result;
  }

  Future<void> run(String action) async {
    if (busy || _disposed) return;
    if (action == 'package-install' &&
        (planId.isEmpty || interrupted || existingInstallation)) {
      return;
    }
    final epoch = ++_epoch;
    busy = true;
    error = '';
    reason = '';
    stage = action == 'package-install' ? 'preparing' : '';
    status = action == 'package-install'
        ? 'Installing verified components. No model inference.'
        : 'Inspecting installation';
    if (action == 'package-install') _startPolling();
    notifyListeners();
    try {
      final value = await call(action,
          confirm: action == 'package-install' ? planId : null);
      if (_disposed || epoch != _epoch) return;
      if (action == 'context') {
        context = value;
        root = (value['root'] as String?) ?? root;
      }
      if (action == 'package-review') {
        review = value;
        interrupted = false;
      }
      if (action == 'package-install') {
        result = value;
        installed = true;
        review = const {};
      }
      status = action == 'package-install'
          ? 'Installation completed'
          : 'Review completed';
    } catch (failure) {
      if (_disposed || epoch != _epoch) return;
      final text = failure.toString().replaceFirst('Bad state: ', '');
      reason = text;
      error = text.isEmpty
          ? 'The operation did not complete.'
          : 'The operation did not complete: $text';
      if (action == 'package-install') {
        interrupted = true;
        review = const {};
      }
      status = 'No automatic retry. Inspect before recovery.';
    } finally {
      _stopPolling();
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  void _startPolling() {
    _poll?.cancel();
    _poll = Timer.periodic(const Duration(milliseconds: 500), (_) {
      unawaited(_readProgress());
    });
  }

  void _stopPolling() {
    _poll?.cancel();
    _poll = null;
  }

  Future<void> _readProgress() async {
    if (_polling || _disposed || !busy) return;
    _polling = true;
    try {
      final reply = await progressBackend.execute(SetupRequest(
          id: 'progress-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'manage',
          selection: const {},
          parameters: {'action': 'package-status', 'root': root}));
      if (!reply.ok || _disposed) return;
      final value = reply.result;
      final nextStage = (value['stage'] as String?) ?? '';
      final done = (value['steps_done'] as num?)?.toInt() ?? 0;
      final total = (value['steps_total'] as num?)?.toInt() ?? 0;
      if (nextStage.isNotEmpty) stage = nextStage;
      stepsDone = done;
      stepsTotal = total;
      notifyListeners();
    } catch (_) {
      // Progress is observational; a missing journal must not fail the install.
    } finally {
      _polling = false;
    }
  }

  Future<void> discover({String endpoint = 'http://127.0.0.1:11434'}) async {
    if (busy || discovering || _disposed) return;
    discovering = true;
    error = '';
    reason = '';
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'discover-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'discover',
          selection: {'role': (selection['role'] as String?) ?? 'combined'},
          endpoint: endpoint));
      if (!reply.ok) {
        throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
      }
      discovery = (reply.result['discovery'] as Json?) ?? const {};
    } catch (failure) {
      if (_disposed) return;
      discovery = const {};
      reason = failure.toString().replaceFirst('Bad state: ', '');
      error =
          'No model inventory was read. ${reason.isEmpty ? 'Check the endpoint.' : reason}';
    } finally {
      if (!_disposed) {
        discovering = false;
        notifyListeners();
      }
    }
  }

  Future<void> cancel() async {
    if (!busy) return;
    interrupted = true;
    review = const {};
    ++_epoch;
    _stopPolling();
    await backend.cancel();
    await progressBackend.cancel();
    if (!_disposed) notifyListeners();
  }

  Future<void> close() async {
    _disposed = true;
    _stopPolling();
    await backend.cancel();
    await backend.close();
    if (!identical(progressBackend, backend)) {
      await progressBackend.cancel();
      await progressBackend.close();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _stopPolling();
    super.dispose();
  }
}

