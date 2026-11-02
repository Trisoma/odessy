import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/settings/controllers/runtime_inventory_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/runtime_inventory.dart';

class InventoryFixture extends DemoApi implements RuntimeSettingsApi {
  Completer<Json>? gate;
  @override
  Future<Json> runtimeSettings() =>
      gate?.future ??
      Future.value({
        'schema_version': 1,
        'instance_id': 'fixture',
        'settings': [
          {
            'name': 'ODESSY_API_TOKEN',
            'kind': 'secret',
            'summary': 'Bearer token',
            'configured': true,
            'sensitive': true,
            'internal': false,
            'editable': false
          },
          {
            'name': 'ODESSY_AUDIO_PLAYER',
            'kind': 'text',
            'summary': 'Playback command',
            'configured': false,
            'sensitive': false,
            'internal': false,
            'editable': false
          }
        ]
      });
}

void main() {
  testWidgets(
      'inventory displays status, filters names and never offers editing',
      (tester) async {
    final session = DesktopSession(InventoryFixture());
    addTearDown(session.dispose);
    await session.start();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: RuntimeInventory(session: session)))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Runtime settings inventory'));
    await tester.pumpAndSettle();
    expect(find.text('2 of 2 settings'), findsOneWidget);
    expect(find.text('ODESSY_API_TOKEN'), findsOneWidget);
    expect(find.textContaining('sensitive'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'audio');
    await tester.pump();
    expect(find.text('1 of 2 settings'), findsOneWidget);
    expect(find.text('ODESSY_API_TOKEN'), findsNothing);
    expect(find.text('ODESSY_AUDIO_PLAYER'), findsOneWidget);
    expect(find.text('Save settings'), findsNothing);
  });

  test('late inventory from a previous connection is discarded', () async {
    final api = InventoryFixture()..gate = Completer<Json>();
    final session = DesktopSession(api);
    addTearDown(session.dispose);
    await session.start();
    final c = RuntimeInventoryController(session);
    addTearDown(c.dispose);
    expect(c.loading, isTrue);
    await session.replace(DemoApi(delay: Duration.zero));
    api.gate!.complete({
      'schema_version': 1,
      'instance_id': 'old',
      'settings': [
        {
          'name': 'ODESSY_API_TOKEN',
          'kind': 'secret',
          'summary': 'token',
          'configured': true,
          'sensitive': true,
          'internal': false,
          'editable': false
        }
      ]
    });
    await Future<void>.delayed(Duration.zero);
    expect(c.settings, isEmpty);
    expect(c.instanceId, isNull);
    expect(c.available, isFalse);
  });
}
