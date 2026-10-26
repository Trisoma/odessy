import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Read-only model metadata; stale provider responses cannot replace new choices.
class ModelCatalogController extends ChangeNotifier {
  ModelCatalogController(SetupBackend source)
      : backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source,
        _ownsBackend = source is SetupMetadataBackend;
  final SetupBackend backend;
  final bool _ownsBackend;
  List<Json> models = [];
  String message = '';
  bool busy = false, _disposed = false;
  int _epoch = 0;
  Future<void> load(String provider,
      {String endpoint = '',
      String credential = '',
      bool refresh = false}) async {
    final epoch = ++_epoch;
    busy = true;
    models = [];
    message = '';
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'catalog-$epoch-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'model-catalog',
          selection: const {},
          parameters: {
            'provider': provider,
            'endpoint': endpoint,
            'credential_env': credential,
            'refresh': refresh
          }));
      if (_disposed || epoch != _epoch) return;
      if (!reply.ok) {
        message = reply.reason.isNotEmpty ? reply.reason : reply.error;
        return;
      }
      models =
          (reply.result['models'] as List? ?? []).whereType<Json>().toList();
      message = reply.result['error'] as String? ??
          (reply.result['status'] == 'empty'
              ? 'No models were listed. You can still enter a custom model ID.'
              : '');
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        message = 'Could not load model choices. Enter a custom ID or retry.';
      }
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
    if (_ownsBackend) backend.close();
    super.dispose();
  }
}
