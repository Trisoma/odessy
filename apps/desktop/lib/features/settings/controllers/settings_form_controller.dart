import 'package:flutter/foundation.dart';
import 'setting_field.dart';

/// Single-field async form seam. Transport closures retain backend authority.
class SettingsFormController<T> extends ChangeNotifier {
  SettingsFormController(
      {required this.field, required this.read, required this.write});
  final SettingField<T> field;
  final Future<T> Function() read;
  final Future<void> Function(T) write;
  bool loading = false;
  bool loaded = false;
  bool _disposed = false;
  bool inspectionRequired = false;
  Future<void>? _write;
  String? error;
  String status = '';
  String get source => field.source;
  String get applyTiming => field.applyTiming;
  bool get dirty => field.dirty;
  bool get canSave =>
      loaded && !loading && !inspectionRequired && dirty && field.error == null;
  void edit(String value) {
    if (_disposed || loading) return;
    field.edit(value);
    error = null;
    status = '';
    notifyListeners();
  }

  Future<void> load() async {
    if (_disposed || loading) return;
    loading = true;
    error = null;
    status = '';
    notifyListeners();
    try {
      final next = await read();
      if (_disposed) return;
      field.load(next);
      inspectionRequired = false;
      loaded = true;
    } catch (_) {
      if (!_disposed) {
        error = 'Could not load settings. Connect the service and retry.';
      }
    } finally {
      if (!_disposed) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> save(
      {String successMessage =
          'Saved by the service. Applies to newly created sessions.'}) async {
    if (!canSave || _disposed) return;
    final operation = _save(successMessage);
    _write = operation;
    await operation;
  }

  Future<void> _save(String successMessage) async {
    final value = field.value as T;
    loading = true;
    error = null;
    status = '';
    notifyListeners();
    try {
      await write(value);
      if (_disposed) return;
      final next = await read();
      if (_disposed) return;
      field.load(next);
      loaded = true;
      status = successMessage;
    } catch (_) {
      if (!_disposed) {
        inspectionRequired = true;
        error =
            'Save or reload failed. No success assumed; draft retained. Reload before retrying.';
      }
    } finally {
      if (!_disposed) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> shutdown() async {
    await _write;
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
