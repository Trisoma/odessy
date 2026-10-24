import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Explicit, reviewed maintenance of one installed version.
///
/// The installation root and the maintenance payload come from the installation
/// itself; the user never supplies an internal Python, wheelhouse or payload path.
class InstalledManageController extends ChangeNotifier {
  InstalledManageController(this.backend, {Json? spec}) : spec = spec ?? const {};

  final SetupBackend backend;
  Json spec;
  Json status = {}, result = {}, review = const {};
  String error = '', reason = '', notice = '';
  bool busy = false, uninstalled = false;
  String? _reviewedAction, _confirmation;
  Json _reviewParameters = const {};
  int _epoch = 0;
  bool _disposed = false;

  String get root => (spec['root'] as String?) ?? '';
  String get payload => (spec['payload'] as String?) ?? '';
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
    busy = false;
    notifyListeners();
  }

  void _failed(int epoch, Object failure) {
    if (_disposed || epoch != _epoch) return;
    final text = failure.toString().replaceFirst('Bad state: ', '');
    error = text.isEmpty ? 'The operation did not complete.' : text;
    notice = 'No automatic retry was performed.';
  }

  Future<void> load() async {
    if (busy || _disposed || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      spec = await _execute('installed-spec', const {});
      status = await _execute('package-status', const {});
      if (_disposed || epoch != _epoch) return;
      notice = '';
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

  Future<void> reviewConfiguration(Json selection, String workspace) =>
      _review('package-configure-review',
          {'selection': selection, 'workspace': workspace});

  Future<void> reviewUninstall() => _review('package-uninstall-review', const {});

  Future<void> reviewService(String workspace, int port) => _review(
      'service-plan', {'workspace': workspace, 'port': port});

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
        'service-install' => 'The user service was registered for this account.',
        _ => 'The operation completed.',
      };

  Future<void> register() async {
    if (busy || _disposed || uninstalled || root.isEmpty) return;
    final epoch = ++_epoch;
    _begin();
    try {
      final value =
          await _execute('package-register', {if (payload.isNotEmpty) 'payload': payload});
      if (_disposed || epoch != _epoch) return;
      result = value;
      notice = 'The Start-menu entry and Installed-apps record were rewritten.';
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
      final value =
          await _execute('package-launch', const {'mode': 'manage'});
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
    super.dispose();
  }
}
