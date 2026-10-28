import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// Startup observations are separate from saved files, form drafts and model tests.
class ReadinessController extends ChangeNotifier {
  RuntimeCapabilities? current;
  OdessyApi? owner;
  String error = '';
  bool busy = false, _disposed = false;
  int _epoch = 0;
  Future<void> bind(OdessyApi api) async {
    final epoch = ++_epoch;
    owner = api;
    current = null;
    error = '';
    busy = false;
    if (api is! RuntimeConfigurationApi) {
      if (!_disposed) notifyListeners();
      return;
    }
    busy = true;
    if (!_disposed) notifyListeners();
    try {
      final result =
          await (api as RuntimeConfigurationApi).runtimeCapabilities();
      if (!_disposed && epoch == _epoch) current = result;
    } catch (e) {
      if (!_disposed &&
          epoch == _epoch &&
          !(e is ApiFailure && e.status == 404)) {
        error =
            'Running configuration could not be inspected. Retry inspection; no changes were applied.';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        busy = false;
        notifyListeners();
      }
    }
  }

  String label(String section, Json saved, {required bool connected}) {
    if (!connected) return 'Running configuration unknown · disconnected';
    final active = current?.configuration;
    if (active == null || saved['configuration'] is! Json) {
      return 'Running configuration unknown';
    }
    try {
      final next =
          RuntimeConfiguration.fromJson(saved['configuration'] as Json);
      if (next.owner != active.owner) return 'Different configuration owner';
      return next.fingerprints[section] == active.fingerprints[section]
          ? 'Matches running configuration'
          : 'Saved · pending restart';
    } catch (_) {
      return 'Saved configuration could not be compared';
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    super.dispose();
  }
}
