import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/connections/connection_dialog.dart';

void main() {
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
