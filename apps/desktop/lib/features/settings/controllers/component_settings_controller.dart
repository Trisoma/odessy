import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

class ComponentSettingsController extends ChangeNotifier {
  ComponentSettingsController(SetupBackend source, {this.root = ''})
      : backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source,
        ownsBackend = source is SetupMetadataBackend;
  final SetupBackend backend;
  final String root;
  final bool ownsBackend;
  Json snapshot = {};
  bool busy = false, _disposed = false;
  String error = '', status = '';
  int _epoch = 0;
  Future<Json> _call(String action,
      {String? section, Json? patch, String? confirm}) async {
    final reply = await backend.execute(SetupRequest(
        id: 'component-${DateTime.now().microsecondsSinceEpoch}',
        operation: 'component-settings',
        selection: const {},
        parameters: {
          'action': action,
          'root': root,
          if (section != null) 'section': section,
          if (patch != null) 'patch': patch,
          if (action != 'load') 'revision': snapshot['revision'],
          if (confirm != null) 'confirm': confirm
        }));
    if (!reply.ok) {
      throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
    }
    return reply.result;
  }

  Future<void> load() async {
    if (busy || _disposed) return;
    final epoch = ++_epoch;
    busy = true;
    error = '';
    status = 'Loading current configuration…';
    notifyListeners();
    try {
      final result = await _call('load');
      if (!_disposed && epoch == _epoch) {
        snapshot = result;
        status = '';
      }
    } catch (e) {
      if (!_disposed && epoch == _epoch) {
        error = e.toString().replaceFirst('Bad state: ', '');
        status = '';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  Future<bool> save(String section, Json patch) async {
    if (busy || _disposed || snapshot['revision'] == null) return false;
    final epoch = ++_epoch;
    busy = true;
    error = '';
    status = 'Validating and saving this section…';
    notifyListeners();
    try {
      final review = await _call('review', section: section, patch: patch);
      if (_disposed || epoch != _epoch) return false;
      final result = await _call('save',
          section: section, patch: patch, confirm: review['plan_id'] as String);
      if (_disposed || epoch != _epoch) return false;
      snapshot = result;
      status =
          'Saved. Restart Odessy to apply. Other components were preserved.';
      return true;
    } catch (e) {
      if (!_disposed && epoch == _epoch) {
        error = e.toString().replaceFirst('Bad state: ', '');
        status =
            'Not saved. Correct the fields, or reload if settings changed.';
      }
      return false;
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    if (ownsBackend) backend.close();
    super.dispose();
  }
}
