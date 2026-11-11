import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../../app/setup/platform_copy.dart';

/// Explicit, reviewed maintenance of one installed version.
///
/// The installation root and the maintenance payload come from the installation
/// itself; the user never supplies an internal Python, wheelhouse or payload path.
class InstalledManageController extends ChangeNotifier {
  InstalledManageController(this.backend,
      {SetupBackend? progressBackend, Json? spec, String? setupPayload})
      : progressBackend = progressBackend ?? backend,
        _suppliedPayload = setupPayload ?? '',
        spec = spec ?? const {};

  final SetupBackend backend;

  /// A second read-only helper, so the maintenance-cache copy can report its
  /// bounded progress while the mutating request is still running.
  final SetupBackend progressBackend;
  final String _suppliedPayload;
  Json spec;
  Json status = {}, result = {}, review = const {};
  Json discovery = const {};
  String error = '', reason = '', notice = '';
  bool busy = false, uninstalled = false, discovering = false;
  String? _reviewedAction, _confirmation;
  Json _reviewParameters = const {};
  int _epoch = 0;
  bool _disposed = false;
  Timer? _progressPoll;
  bool _polling = false;

  /// The last bounded registration counters, when the service reports them.
  Json registrationProgress = const {};

  /// Copy progress while the maintenance cache is prepared, or null when unknown.
  double? get progress {
    if (registrationProgress['status'] != 'running') return null;
    final done = registrationProgress['files_done'];
    final total = registrationProgress['files_total'];
    if (done is! int || total is! int || total <= 0) return null;
    return (done / total).clamp(0.0, 1.0);
  }

  String get progressLabel {
    if (registrationProgress['status'] != 'running') return '';
    final done = registrationProgress['files_done'];
    final total = registrationProgress['files_total'];
    if (done is int && total is int && total > 0) {
      return 'Preparing the maintenance copy: $done of $total files.';
    }
    return 'Preparing the maintenance copy.';
  }

  String get root => (spec['root'] as String?) ?? '';
  String get payload => _suppliedPayload.isNotEmpty
      ? _suppliedPayload
      : status.containsKey('payload')
          ? (status['payload'] as String?) ?? ''
          : (spec['payload'] as String?) ?? '';
  String? get reviewedAction => _reviewedAction;
  bool get canConfirm =>
      !busy && !uninstalled && _reviewedAction != null && _confirmation != null;

  Json get configuration {
    final value = status['configuration'];
    return value is Map<String, dynamic> ? value : const {};
  }

  List<String> get versions {
    final value = status['versions'];
    if (value is! List) return const [];
    return value.whereType<String>().toList(growable: false);
  }

  String get activeVersion => (status['active'] as String?) ?? '';

  String get role => (configuration['role'] as String?) ?? 'combined';

  bool get modelConfigured => configuration['model_configured'] == true;

  String get workspace =>
      (configuration['workspace'] as String?) ??
      (spec['workspace'] as String?) ??
      '';

  Future<Json> _execute(String action, Json parameters) async {
    final reply = await backend.execute(SetupRequest(
        id: 'installed-${DateTime.now().microsecondsSinceEpoch}',
        operation: 'manage',
        selection: const {},
        parameters: {'action': action, 'root': root, ...parameters}));
    if (!reply.ok) {
      throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
    }
    return reply.result;
  }

  void _begin() {
    error = '';
    reason = '';
    notice = '';
    _reviewedAction = null;
    _confirmation = null;
    _reviewParameters = const {};
    review = const {};
    busy = true;
    notifyListeners();
  }

  void _end(int epoch) {
    if (_disposed || epoch != _epoch) return;
    _stopProgress();
    busy = false;
    notifyListeners();
  }

  void _startProgress() {
    _stopProgress();
    if (identical(progressBackend, backend)) return;
    registrationProgress = const {};
    _progressPoll = Timer.periodic(
        const Duration(milliseconds: 500), (_) => _pollProgress());
  }

  void _stopProgress() {
    _progressPoll?.cancel();
    _progressPoll = null;
    _polling = false;
  }

  Future<void> _pollProgress() async {
    if (_polling || _disposed) return;
    _polling = true;
    try {
      final reply = await progressBackend.execute(SetupRequest(
          id: 'manage-progress-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'manage',
          selection: const {},
          parameters: {'action': 'package-status', 'root': root}));
      if (_disposed) return;
      final value = reply.ok ? reply.result['registration_progress'] : null;
      if (value is Map<String, dynamic>) {
        registrationProgress = value;
        notifyListeners();
      }
    } catch (_) {
      // A failed progress read must never fail the operation it observes.
    } finally {
      _polling = false;
    }
  }

  /// Ask the running helper to stop before its next bounded step.
  Future<void> cancel() async {
    if (!busy || _disposed) return;
    _stopProgress();
    notice =
        'Cancellation requested. Completed steps are not undone; inspect status before retrying.';
    notifyListeners();
    await backend.cancel();
    if (!identical(progressBackend, backend)) await progressBackend.cancel();
  }

  void _failed(int epoch, Object failure) {
    if (_disposed || epoch != _epoch) return;
    final text = failure.toString().replaceFirst('Bad state: ', '');
    error = text.isEmpty ? 'The operation did not complete.' : text;
    notice = 'No automatic retry was performed.';
  }

  Future<void> load() async {
    if (busy || _disposed) return;
    // Returning silently left the page showing empty tiles and no explanation.
    if (root.isEmpty) {
      error = 'This window has no installation folder to manage.';
      notifyListeners();
      return;
    }
    final epoch = ++_epoch;
    _begin();
    try {
      try {
        spec = await _execute('installed-spec', const {});
        notice = '';
      } catch (failure) {
        spec = {'root': root, if (spec['portable'] == true) 'portable': true};
        _failed(epoch, failure);
      }
      status = await _execute('package-status', const {});
      if (_disposed || epoch != _epoch) return;
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  Future<void> refreshStatus() async {
    if (busy || _disposed || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute('package-status', const {});
      if (_disposed || epoch != _epoch) return;
      status = value;
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  Future<void> verify() async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute('package-verify', const {});
      if (_disposed || epoch != _epoch) return;
      result = value;
      notice = value['valid'] == true
          ? 'Installed files match the recorded hashes.'
          : 'Changed or missing files were found. Repair from the bundled payload.';
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  Future<void> _review(String action, Json parameters) async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute(action, parameters);
      if (_disposed || epoch != _epoch) return;
      final token = value['plan_id'] ?? value['service_plan_id'];
      if (token is! String || token.isEmpty) {
        throw const FormatException('The review did not return an identity.');
      }
      review = value;
      _reviewedAction = action;
      _confirmation = token;
      _reviewParameters = parameters;
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  Future<void> reviewRepair() => _review('package-repair-review', {
        if (payload.isNotEmpty) 'payload': payload,
      });

  Future<void> reviewRollback(String version) =>
      _review('package-rollback-review', {'version': version});

  Future<void> reviewConfiguration(Json selection, String workspace) => _review(
      'package-configure-review',
      {'selection': selection, 'workspace': workspace});

  Future<void> reviewUninstall() =>
      _review('package-uninstall-review', const {});

  /// Explicit, bounded inventory of the configured local endpoint only.
  Future<void> discover({String endpoint = 'http://127.0.0.1:11434'}) async {
    if (busy || discovering || _disposed) return;
    discovering = true;
    error = '';
    reason = '';
    discovery = const {};
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'discover-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'discover',
          selection: {'role': role},
          endpoint: endpoint));
      if (!reply.ok) {
        throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
      }
      discovery = (reply.result['discovery'] as Json?) ?? const {};
    } catch (failure) {
      if (_disposed) return;
      reason = failure.toString().replaceFirst('Bad state: ', '');
      error = reason.isEmpty
          ? 'No model inventory was read.'
          : 'No model inventory was read: $reason';
    } finally {
      if (!_disposed) {
        discovering = false;
        notifyListeners();
      }
    }
  }

  Future<void> reviewService(String workspace, int port) =>
      _review('service-plan', {'workspace': workspace, 'port': port});

  Future<void> confirm() async {
    if (!canConfirm) return;
    final action = _reviewedAction!;
    final confirmation = _confirmation!;
    final parameters = _reviewParameters;
    final mutating = switch (action) {
      'package-repair-review' => 'package-repair',
      'package-rollback-review' => 'package-rollback',
      'package-configure-review' => 'package-configure',
      'package-uninstall-review' => 'package-uninstall',
      'service-plan' => 'service-install',
      _ => null,
    };
    if (mutating == null) {
      error = 'This review cannot be applied.';
      notifyListeners();
      return;
    }
    if (busy || _disposed) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute(mutating, {
        ...parameters,
        'confirm': confirmation,
      });
      if (_disposed || epoch != _epoch) return;
      result = value;
      if (mutating == 'package-uninstall') {
        uninstalled = true;
        notice =
            'Odessy was uninstalled from this computer. Models, credentials, settings and unrelated files were preserved.';
      } else {
        notice = _completedNotice(mutating);
        if (mutating == 'package-configure') {
          spec = await _execute('installed-spec', const {});
          if (_disposed || epoch != _epoch) return;
        }
        status = await _execute('package-status', const {});
      }
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  String _completedNotice(String action) => switch (action) {
        'package-repair' =>
          'A new version was installed and activated from the bundled payload.',
        'package-rollback' => 'The previous owned version is active again.',
        'package-configure' =>
          'Configuration saved. The next launch uses it; nothing was started.',
        'service-install' =>
          'The user service was registered for this account.',
        _ => 'The operation completed.',
      };

  Future<void> register() async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    _startProgress();
    try {
      final value = await _execute(
          'package-register', {if (payload.isNotEmpty) 'payload': payload});
      if (_disposed || epoch != _epoch) return;
      result = value;
      notice = registrationRewrittenCopy;
      status = await _execute('package-status', const {});
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  Future<void> service(String action) async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute(action, const {});
      if (_disposed || epoch != _epoch) return;
      result = value;
      notice = switch (action) {
        'start' => 'The user service was started.',
        'stop' => 'The user service was stopped.',
        'service-remove' => 'The recorded user service was removed.',
        _ => 'Service status read.',
      };
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  Future<void> launch() async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute('package-launch', const {});
      if (_disposed || epoch != _epoch) return;
      result = value;
      notice = 'Odessy was started from the installed application.';
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  /// Opens the maintenance copy cached outside the installation, which is what
  /// makes uninstall possible while this application is running.
  Future<void> openMaintenance() async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value = await _execute('package-launch', const {'mode': 'manage'});
      if (_disposed || epoch != _epoch) return;
      result = value;
      notice =
          'The maintenance window was opened. Uninstall runs from there so the removed files are not in use.';
    } catch (failure) {
      _failed(epoch, failure);
    } finally {
      _end(epoch);
    }
  }

  void cancelReview() {
    if (busy) return;
    _reviewedAction = null;
    _confirmation = null;
    review = const {};
    notifyListeners();
  }

  void dismissNotice() {
    if (busy) return;
    notice = '';
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _stopProgress();
    super.dispose();
  }
}
