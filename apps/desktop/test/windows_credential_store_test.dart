import 'dart:io';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/connections/windows_credential_store.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('odessy/credential_vault_test');
  test('vault binds operations to a normalized loopback origin', () async {
    final values = <String, String>{};
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      final args = Map<String, dynamic>.from(call.arguments as Map);
      final origin = args['origin'] as String;
      switch (call.method) {
        case 'write':
          values[origin] = args['token'] as String;
        case 'read':
          return values[origin];
        case 'remove':
          values.remove(origin);
      }
      return null;
    });
    addTearDown(() => TestDefaultBinaryMessengerBinding
        .instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null));
    final store = WindowsCredentialStore(channel: channel);
    await store.write('http://127.0.0.1:8765/', 'fixture');
    expect(await store.read('http://127.0.0.1:8765'), 'fixture');
    expect(await store.read('http://127.0.0.1:8766'), isNull);
    expect(() => store.read('http://example.com:8765'), throwsFormatException);
    expect(() => store.read('http://user:pass@localhost:8765'),
        throwsFormatException);
    await store.remove('http://127.0.0.1:8765');
    expect(await store.read('http://127.0.0.1:8765'), isNull);
  }, skip: !Platform.isWindows);
}
