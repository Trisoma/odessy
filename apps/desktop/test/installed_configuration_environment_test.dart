import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';

void main() {
  final root = Platform.environment['ODESSY_TEST_INSTALLED_ROOT'];
  test('installed configuration loads despite an inherited setup PYTHONPATH',
      () async {
    final record =
        jsonDecode(File('$root/installation.json').readAsStringSync()) as Map;
    final executable =
        '$root/versions/${record['active']}/desktop/odessy_desktop.exe';
    final backend = LocalSetupBackend(executablePath: executable);
    try {
      expect(backend.isolated, isTrue);
      final reply = await backend.execute(SetupRequest(
          id: 'configuration-check',
          operation: 'component-settings',
          selection: const {},
          parameters: {'action': 'load', 'root': root}));
      expect(reply.ok, isTrue, reason: reply.reason);
      expect(reply.result['revision'], isA<String>());
      expect(reply.result['selection'], isA<Map>());
    } finally {
      await backend.close();
    }
  },
      skip: root == null
          ? 'Requires an explicitly selected test installation.'
          : false);
}
