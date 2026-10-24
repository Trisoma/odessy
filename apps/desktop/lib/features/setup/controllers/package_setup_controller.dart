import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

import 'setup_stage.dart';

/// One packaged installation flow: verified context, exact review, journal-backed
/// progress and a single mutation. Late replies from a cancelled attempt are
/// discarded by generation rather than applied.
class PackageSetupController extends ChangeNotifier {
  PackageSetupController(this.backend, {SetupBackend? progressBackend})
      : progressBackend = progressBackend ?? backend;

  /// Bounded so a noisy run can never grow the UI's memory without limit.
  static const maxOutputLines = 2000;
  static const maxOutputCharacters = 256 * 1024;

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
  List<SetupStage> stages = newPackagedStages();
  final List<String> output = [];
  DateTime? attemptStartedAt;
  Timer? _ticker;

  Duration get elapsed => attemptStartedAt == null
      ? Duration.zero
      : DateTime.now().difference(attemptStartedAt!);

  /// Shown only when the engine is genuinely slow; no remaining-time estimate.
  bool get longRunning => busy && elapsed.inSeconds >= 15;

  List<SetupStage> get visibleStages => stages;

  /// Highest engine log sequence already shown, so a poll never repeats a line.
  int _logCursor = 0;
  bool _truncationNoted = false;

  /// Only redacted lines the engine kept, appended in sequence order. A missing or
  /// malformed entry is skipped; the pane never fabricates output.
  void _appendEngineLogs(Json value) {
    final logs = value['logs'];
    if (logs is! List) return;
    if (value['logs_truncated'] == true && !_truncationNoted) {
      _truncationNoted = true;
      _log('Earlier output is no longer in the bounded journal');
    }
    for (final entry in logs) {
      if (entry is! Map) continue;
      final seq = (entry['seq'] as num?)?.toInt() ?? 0;
      if (seq <= _logCursor) continue;
      _logCursor = seq;
      final line = entry['line'];
      if (line is! String || line.trim().isEmpty) continue;
      final stageName = entry['stage'] is String ? entry['stage'] as String : '';
      final id = journalStageIds[stageName] ?? stageName;
      final label = packagedStageLabels[id] ?? id;
      final stream = entry['stream'];
      final prefix = stream == 'stderr'
          ? (label.isEmpty ? 'error' : '$label error')
          : (label.isEmpty ? 'engine' : label);
      _log('$prefix: ${line.trim()}');
    }
  }

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

  void _log(String message) {
    final stamp = DateTime.now()
        .toIso8601String()
        .substring(11, 19);
    output.add('$stamp  $message');
    while (output.length > maxOutputLines) {
      output.removeAt(0);
    }
    var size = output.fold<int>(0, (sum, line) => sum + line.length + 1);
    while (size > maxOutputCharacters && output.length > 1) {
      size -= output.removeAt(0).length + 1;
    }
  }

  void _advanceStages(
      {String? engineStage,
      bool complete = false,
      bool failed = false,
      bool cancelled = false}) {
    final before = stages.map((s) => '${s.id}:${s.state.name}').join(',');
    stages = advanceStages(stages, engineStage ?? stage,
        DateTime.now(),
        busy: !complete && !failed && !cancelled,
        complete: complete,
        failed: failed,
        cancelled: cancelled);
    final after = stages.map((s) => '${s.id}:${s.state.name}').join(',');
    if (before != after) {
      final running = stages.where((s) => s.state == SetupStageState.running);
      if (running.isNotEmpty) {
        _log('Stage: ${running.first.label}');
      } else if (complete) {
        _log('All packaged stages completed');
      }
    }
  }

  void _startTicker() {
    _ticker?.cancel();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!_disposed && busy) notifyListeners();
    });
  }

  void _stopTicker() {
    _ticker?.cancel();
    _ticker = null;
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
    if (action == 'package-install') {
      attemptStartedAt = DateTime.now();
      stages = newPackagedStages();
      output.clear();
      _logCursor = 0;
      _truncationNoted = false;
      _log('Preparing the reviewed installation');
      _advanceStages(engineStage: 'preparing');
      _startPolling();
      _startTicker();
    }
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
        stage = 'activated';
        _log('Installation activated; packaging finished');
        _advanceStages(engineStage: 'activated', complete: true);
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
        _log('Installation stopped: $text');
        _advanceStages(failed: true);
      }
      status = 'No automatic retry. Inspect before recovery.';
    } finally {
      _stopPolling();
      _stopTicker();
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
      await readProgress();
    } finally {
      _polling = false;
    }
  }

  /// One snapshot applied by generation. Exposed so log de-duplication is
  /// testable without a live engine; the view never calls it directly.
  @visibleForTesting
  Future<void> readProgress() async {
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
      final changed = nextStage.isNotEmpty && nextStage != stage;
      if (nextStage.isNotEmpty) stage = nextStage;
      if (done != stepsDone && (done % 25 == 0 || changed)) {
        _log('Packaged steps: $done of $total');
      }
      stepsDone = done;
      stepsTotal = total;
      _appendEngineLogs(value);
      _advanceStages();
      notifyListeners();
    } catch (_) {
      // Progress is observational; a missing journal must not fail the install.
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
    _log('Cancellation requested; no further steps will start');
    _advanceStages(cancelled: true);
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
    _stopTicker();
    super.dispose();
  }
}

