import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';

void main() {
  test(
      'installation and other mutations are not killed by a wall-clock deadline',
      () {
    for (final action in [
      'package-install',
      'package-repair',
      'package-register',
      'package-uninstall',
      'voice-install',
      'voice-python-install',
    ]) {
      final request = SetupRequest(
          id: 'test',
          operation: 'manage',
          selection: const {},
          parameters: {'action': action});
      expect(setupRequestTimeout(request, const Duration(seconds: 30)), isNull,
          reason:
              '$action may make persistent changes and must be cancellable');
    }
  });

  test('read-only operations retain bounded timeouts', () {
    final status = SetupRequest(
        id: 'test',
        operation: 'manage',
        selection: const {},
        parameters: {'action': 'package-status'});
    expect(setupRequestTimeout(status, const Duration(seconds: 30)),
        const Duration(minutes: 12));
    final discovery = SetupRequest(
        id: 'test',
        operation: 'discover',
        selection: const {},
        parameters: const {});
    expect(setupRequestTimeout(discovery, const Duration(seconds: 30)),
        const Duration(seconds: 30));
  });
}
