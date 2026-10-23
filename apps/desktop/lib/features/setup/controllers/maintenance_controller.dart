import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Explicit native changes, bound to the reviewed local field snapshot.
class MaintenanceController extends ChangeNotifier {
  MaintenanceController(this.backend);
  final SetupBackend backend;
  Json fields = {}, result = {};
  String error = '', status = 'Inspect local state before making changes';
  String? reviewedAction, _confirmation;
  bool busy = false,
      needsInspection = false,
      _disposed = false,
      _mutating = false;
  int _epoch = 0;
  bool get canApply =>
      !busy &&
      !needsInspection &&
      reviewedAction != null &&
      _confirmation != null;
  void update(Json value) {
    if (busy || needsInspection) return;
    fields = Map.unmodifiable(value);
    reviewedAction = null;
    _confirmation = null;
    result = {};
    error = '';
    notifyListeners();
  }

  Json _parameters(String action) {
    final pair = action.startsWith('pair:');
    final actual = pair ? action.substring(5) : action;
    final allowed = pair
        ? {
            'link',
            'alias',
            'certificate',
            'fingerprint',
            'address',
            'workspace',
            'output'
          }
        : {
            'root',
            'bundle',
            'config',
            'role',
            'version',
            'workspace',
            'port',
            'node_link',
            'node_server',
            'listen'
          };
    final values = <String, dynamic>{
      'action': actual,
      for (final entry in fields.entries)
        if (allowed.contains(entry.key) && entry.value != '')
          entry.key: entry.value
    };
    if (!pair && values.containsKey('config')) values.remove('role');
    return values;
  }

  Future<Json> _call(String action,
      {bool review = false, String? confirm}) async {
    final pair = action.startsWith('pair:');
    final parameters = _parameters(action);
    if (review && pair) parameters['review'] = true;
    if (confirm != null) parameters['confirm'] = confirm;
    final request = SetupRequest(
        id: 'native-${DateTime.now().microsecondsSinceEpoch}',
        operation: pair ? 'pair' : 'manage',
        selection: const {},
        parameters: parameters);
    final reply = await backend.execute(request);
    if (!reply.ok) throw StateError(reply.error);
    return reply.result;
  }

  Future<void> inspect([String action = 'inspect']) => _run(action, false);
  Future<void> review(String action) => _run(action, true);
  Future<void> _run(String action, bool reviewing) async {
    if (busy ||
        _disposed ||
        (needsInspection && action != 'inspect' && action != 'pair:list')) {
      return;
    }
    final epoch = ++_epoch;
    busy = true;
    error = '';
    reviewedAction = null;
    _confirmation = null;
    notifyListeners();
    try {
      final readAction = reviewing
          ? (action.startsWith('pair:')
              ? action
              : action == 'service-install'
                  ? 'service-plan'
                  : action == 'repair'
                      ? 'repair-plan'
                      : action == 'install'
                          ? 'plan'
                          : 'inspect')
          : action;
      final value = await _call(readAction, review: reviewing);
      if (_disposed || epoch != _epoch) return;
      result = value;
      if (reviewing) {
        final token = value['plan_id'] ??
            value['service_plan_id'] ??
            value['installation_id'];
        if (token is! String || token.isEmpty) {
          throw const FormatException('Missing confirmation identity');
        }
        reviewedAction = action;
        _confirmation = token;
        status = 'Review the exact action and details, then confirm once';
      } else {
        needsInspection = false;
        status = 'Inspection completed; no mutation repeated';
      }
    } catch (_) {
      if (epoch == _epoch) {
        error =
            'Operation failed. Check local inputs, permissions and component availability.';
        status = 'No automatic retry';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> apply() async {
    if (!canApply || _disposed) return;
    final action = reviewedAction!,
        confirmation = _confirmation!,
        epoch = ++_epoch;
    busy = true;
    _mutating = true;
    reviewedAction = null;
    _confirmation = null;
    error = '';
    status = 'Applying reviewed local operation';
    notifyListeners();
    try {
      final value = await _call(action, confirm: confirmation);
      if (_disposed || epoch != _epoch) return;
      result = value;
      status = 'Operation completed. Inspect current state before continuing';
      needsInspection = true;
    } catch (_) {
      if (epoch == _epoch) {
        needsInspection = true;
        error = 'Outcome may be partial. Inspect state before any retry.';
        status = 'Inspection required';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        _mutating = false;
        notifyListeners();
      }
    }
  }

  Future<void> cancel() async {
    if (!busy || _disposed) return;
    ++_epoch;
    needsInspection = needsInspection || _mutating;
    reviewedAction = null;
    _confirmation = null;
    await backend.cancel();
    if (_disposed) return;
    busy = false;
    _mutating = false;
    status = 'Cancelled; completed effects remain. Inspect before retrying';
    notifyListeners();
  }

  Future<void> shutdown() async {
    await cancel();
    await backend.close();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    unawaited(backend.close());
    super.dispose();
  }
}
