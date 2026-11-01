import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

class ModelCredentialController extends ChangeNotifier {
  ModelCredentialController(SetupBackend source,
      {required this.provider, required this.endpoint, required this.reference})
      : backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source,
        ownsBackend = source is SetupMetadataBackend;
  final SetupBackend backend;
  final String provider, endpoint, reference;
  final bool ownsBackend;
  bool busy = false, _disposed = false;
  ModelCredentialStatus? current;
  String message = '', error = '';
  Future<void> load() => _call('describe');
  Future<void> save(String value, {required bool persistent}) =>
      _call('set', value: value, storage: persistent ? 'keyring' : 'session');
  Future<void> remove() => _call('remove',
      storage: current?.source == 'session' ? 'session' : 'keyring');

  Future<void> _call(String action, {String? value, String? storage}) async {
    if (busy || _disposed) return;
    busy = true;
    error = '';
    message = '';
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'credential-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'model-credential',
          selection: const {},
          parameters: {
            'action': action,
            'provider': provider,
            'endpoint': endpoint,
            'credential_env': reference,
            if (value != null) 'value': value,
            if (storage != null) 'storage': storage,
          }));
      if (_disposed) return;
      if (!reply.ok) {
        error = reply.reason.isNotEmpty
            ? reply.reason
            : 'Credential operation failed. Inspect status before retrying.';
        return;
      }
      current = ModelCredentialStatus.fromJson(reply.result);
      if (action == 'set') {
        message = storage == 'keyring'
            ? 'Saved securely. Future requests use this key; no model test was run.'
            : 'Available for this session only. Closing Odessy forgets this key.';
      }
      if (action == 'remove') {
        message =
            'Removed the selected credential source. Other sources, if present, are shown below.';
      }
    } catch (_) {
      if (!_disposed) {
        error =
            'Credential operation could not be confirmed. Refresh status before retrying.';
      }
    } finally {
      busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    if (ownsBackend) backend.close();
    super.dispose();
  }
}
