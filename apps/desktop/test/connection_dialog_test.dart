import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/connections/connection_dialog.dart';

class TestVault extends MemoryCredentialStore {
  int writes = 0, removals = 0;
  bool fail = false;
  @override
  bool get persistsAcrossRestarts => true;
  @override
  Future<void> write(String identity, String token) async {
    writes++;
    if (fail) throw StateError('vault failure');
    await super.write(identity, token);
  }

  @override
  Future<void> remove(String identity) async {
    removals++;
    await super.remove(identity);
  }
}

void main() {
  testWidgets('persistent storage is opt-in and failure does not connect',
      (tester) async {
    final vault = TestVault()..fail = true;
    final memory = MemoryCredentialStore();
    var connected = false;
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () async {
                  final api =
                      await showConnectionDialog(context, memory, vault: vault);
                  connected = api != null;
                  api?.close();
                },
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('connection-token')), 'fixture-token');
    await tester.tap(find.text('Save token in Windows Credential Manager'));
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();
    expect(vault.writes, 1);
    expect(connected, isFalse);
    expect(await memory.read('http://127.0.0.1:8765'), isNull);
    expect(find.textContaining('Credential storage must be available'),
        findsOneWidget);
    await tester.tap(find.text('Save token in Windows Credential Manager'));
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();
    expect(connected, isTrue);
    expect(vault.writes, 1);
  });

  testWidgets('changing address requires a fresh choice to save a token',
      (tester) async {
    final vault = TestVault();
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () => showConnectionDialog(
                    context, MemoryCredentialStore(),
                    vault: vault),
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save token in Windows Credential Manager'));
    await tester.enterText(
        find.byKey(const Key('connection-endpoint')), 'http://127.0.0.1:8876');
    await tester.pumpAndSettle();
    expect(tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).value,
        false);
    await tester.enterText(
        find.byKey(const Key('connection-token')), 'new-token');
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();
    expect(vault.writes, 0);
  });

  testWidgets('saved token is loaded and forgotten only on request',
      (tester) async {
    final vault = TestVault();
    await vault.write('http://127.0.0.1:8765', 'fixture-token');
    final memory = MemoryCredentialStore();
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () =>
                    showConnectionDialog(context, memory, vault: vault),
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    final field = find.byKey(const Key('connection-token'));
    expect(tester.widget<TextField>(field).controller!.text, isEmpty);
    await tester.tap(find.text('Load saved token'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(field).controller!.text, 'fixture-token');
    await tester.tap(find.text('Forget saved token'));
    await tester.pumpAndSettle();
    expect(await vault.read('http://127.0.0.1:8765'), isNull);
    expect(tester.widget<TextField>(field).controller!.text, isEmpty);
    expect(vault.removals, 1);
  });

  testWidgets('changing origin clears its cached token', (tester) async {
    final store = MemoryCredentialStore();
    await store.write('http://127.0.0.1:8765', 'origin-a-secret');
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () => showConnectionDialog(context, store),
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    final token = find.byKey(const Key('connection-token'));
    expect(tester.widget<TextField>(token).controller!.text, 'origin-a-secret');
    await tester.enterText(
        find.byKey(const Key('connection-endpoint')), 'http://127.0.0.1:8876');
    expect(tester.widget<TextField>(token).controller!.text, isEmpty);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
  });
}
