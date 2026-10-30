import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// The audio inventory for the selected backend.
///
/// Observation only: the helper lists device names and never opens a microphone,
/// plays audio or starts a service. Kept separate from [VoiceDiscoveryController] so
/// a failed enumeration cannot suppress asset suggestions, and separate from the
/// component test controller so it cannot appear to be a test result.
class AudioInventoryController extends ChangeNotifier {
  AudioInventoryController(SetupBackend source)
      : ownsBackend = source is SetupMetadataBackend,
        backend = source is SetupMetadataBackend
            ? (source as SetupMetadataBackend).createMetadataReader()
            : source;
  final SetupBackend backend;
  final bool ownsBackend;
  bool busy = false, _disposed = false;
  int _epoch = 0;
  List<Json> devices = const [];
  Json directions = const {};
  String backendName = '', status = '', message = '', error = '';

  /// Devices that can serve one direction, in the order the backend listed them.
  List<Json> forDirection(String direction) =>
      [for (final device in devices) if (device[direction] == true) device];

  String directionMessage(String direction) =>
      '${(directions[direction] as Map?)?['message'] ?? ''}';

  void invalidate() {
    _epoch++;
    devices = const [];
    directions = const {};
    backendName = '';
    status = '';
    message = '';
    error = '';
    if (!_disposed) notifyListeners();
  }

  Future<void> detect(Json selection) async {
    if (_disposed) return;
    final epoch = ++_epoch;
    busy = true;
    error = '';
    notifyListeners();
    try {
      final reply = await backend.execute(SetupRequest(
          id: 'voice-devices-${DateTime.now().microsecondsSinceEpoch}',
          operation: 'component-probe',
          selection: selection,
          // run:false is observational only — no capture, no playback, no inference.
          parameters: const {'section': 'voice', 'run': false}));
      if (_disposed || epoch != _epoch) return;
      if (reply.ok) {
        devices = [
          for (final device in (reply.result['devices'] as List? ?? const []))
            if (device is Map) Map<String, dynamic>.from(device)
        ];
        directions = reply.result['directions'] is Map
            ? Map<String, dynamic>.from(reply.result['directions'] as Map)
            : const {};
        backendName = '${reply.result['backend'] ?? ''}';
        status = '${reply.result['status'] ?? ''}';
        message = '${reply.result['message'] ?? ''}';
      } else {
        error = reply.reason.isEmpty ? reply.error : reply.reason;
      }
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        error =
            'Device detection failed. Saved identifiers were kept; type one under Advanced.';
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
    if (ownsBackend) backend.close();
    super.dispose();
  }
}
