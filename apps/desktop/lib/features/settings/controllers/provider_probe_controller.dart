import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Explicit probes on a separate owned helper. Never submit a chat turn.
class ProviderProbeController extends ChangeNotifier {
  ProviderProbeController(SetupBackend source)
      : ownsBackend = source is SetupMetadataBackend,
        backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source;
  final SetupBackend backend;
  final bool ownsBackend;
  bool busy = false, _disposed = false;
  int _epoch = 0;
  Json result = {};
  String error = '';
  void invalidate() {
    _epoch++;
    result = {};
    error = '';
    if (!_disposed) notifyListeners();
  }

  Future<void> run(Json selection, String section,
      {bool test = false, String audio = ''}) async {
    if (busy || _disposed) return;
    final epoch = ++_epoch;
    busy = true;
    result = {};
    error = '';
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'probe-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'component-probe',
          selection: selection,
          parameters: {
            'section': section,
            'run': test,
            'consent': test,
            if (audio.isNotEmpty) 'audio': audio
          }));
      if (!_disposed && epoch == _epoch) {
        if (reply.ok) {
          result = reply.result;
        } else {
          error = reply.reason.isEmpty ? reply.error : reply.reason;
        }
      }
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        error =
            'Test interrupted or unavailable. No chat or tool work was retried.';
      }
    } finally {
      busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> cancel() async {
    invalidate();
    await backend.cancel();
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    if (ownsBackend) backend.close();
    super.dispose();
  }
}
