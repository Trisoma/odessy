import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// One foreground setup operation. Save is explicit; recovery is read-only.
class SetupController extends ChangeNotifier {
  SetupController(this.backend);
  final SetupBackend backend;
  Json _selection = {'role': 'combined'};
  String output = '',
      format = 'selection',
      error = '',
      status = 'Not configured';
  Json? reviewResult, diagnostics, inventory;
  bool busy = false, recoveryRequired = false, saved = false;
  bool _disposed = false, _cancelling = false;
  int _epoch = 0, _request = 0;
  Json get selection => _selection;
  bool get canSave =>
      !busy &&
      !recoveryRequired &&
      !saved &&
      output.trim().isNotEmpty &&
      reviewResult != null;

  void update(Json selection, String destination,
      {String exportFormat = 'selection'}) {
    if (busy || recoveryRequired || _disposed) return;
    _selection =
        SetupRequest(id: 'snapshot', operation: 'review', selection: selection)
            .selection;
    output = destination.trim();
    format = exportFormat;
    reviewResult = null;
    diagnostics = null;
    inventory = null;
    saved = false;
    error = '';
    status = recoveryRequired
        ? 'Inspect the previous save before continuing'
        : 'Changes need review';
    notifyListeners();
  }

  Future<void> _run(String operation, {String endpoint = ''}) async {
    if (busy || _disposed) return;
    if (operation == 'save' && !canSave) return;
    if (recoveryRequired && operation != 'recover') {
      error = 'Inspect the destination before starting another operation.';
      notifyListeners();
      return;
    }
    final epoch = ++_epoch;
    final request = SetupRequest(
        id: 'setup-${DateTime.now().microsecondsSinceEpoch}-${++_request}',
        operation: operation,
        selection:
            operation == 'discover' ? {'role': _selection['role']} : _selection,
        output: output,
        format: format,
        confirmPlan:
            operation == 'save' ? reviewResult!['plan_id'] as String : '',
        endpoint: endpoint);
    busy = true;
    error = '';
    status = '$operation in progress';
    notifyListeners();
    try {
      final reply = await backend.execute(request);
      if (_disposed || epoch != _epoch) return;
      if (!reply.ok) throw StateError(reply.error);
      switch (operation) {
        case 'review':
          if (reply.result['plan_id'] is! String ||
              (reply.result['plan_id'] as String).isEmpty) {
            throw const FormatException('Missing reviewed plan identity.');
          }
          reviewResult = reply.result;
        case 'discover':
          inventory = reply.result['discovery'] as Json?;
        case 'verify':
          diagnostics = reply.result['verification'] as Json?;
        case 'save':
          saved = reply.result['saved'] == true;
          if (!saved) throw const FormatException('Save was not confirmed.');
        case 'recover':
          final recovery = reply.result['recovery'];
          if (!{'matches', 'missing', 'conflict'}.contains(recovery)) {
            throw const FormatException('Invalid recovery result.');
          }
          recoveryRequired = false;
          saved = recovery == 'matches';
          reviewResult = null;
          status = switch (recovery) {
            'matches' =>
              'Configuration exists and matches. Save was not repeated.',
            'missing' => 'No export found. Review again before retrying.',
            _ =>
              'Existing file differs. Choose a new destination; nothing was replaced.'
          };
      }
      if (operation != 'recover') {
        status = operation == 'save'
            ? 'Configuration saved — components are not installed or started'
            : '$operation completed';
      }
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        if (operation == 'save') recoveryRequired = true;
        error =
            'Setup could not complete. Check the Python executable, selection and destination. '
            'The helper needs Odessy installed, or an explicit source root. Existing files are never overwritten.';
        status = operation == 'save'
            ? 'Save outcome uncertain — inspect destination'
            : 'Operation failed; retry is available';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  Future<void> review() => _run('review');
  Future<void> discover(String endpoint) =>
      _run('discover', endpoint: endpoint);
  Future<void> verify() => _run('verify');
  Future<void> save() => _run('save');
  Future<void> recover() => _run('recover');
  Future<void> cancel() async {
    if (!busy || _disposed || _cancelling) return;
    _cancelling = true;
    if (status.startsWith('save ')) recoveryRequired = true;
    ++_epoch;
    status = 'Cancelling owned setup helper';
    notifyListeners();
    await backend.cancel();
    _cancelling = false;
    if (_disposed) return;
    busy = false;
    reviewResult = null;
    status = recoveryRequired
        ? 'Save interrupted — inspect destination before retrying'
        : 'Cancelled; no model/tool work was submitted';
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
