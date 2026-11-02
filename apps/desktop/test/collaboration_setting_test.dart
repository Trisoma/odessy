import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/collaboration_setting_dialog.dart';
import 'package:odessy_desktop/features/settings/controllers/collaboration_setting_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';

class ContextFixture extends DemoApi
    implements ContextLimitApi, RoundLimitApi, CollaborationEnabledApi {
  @override
  bool get isDemo => false;
  int value = 8000, revision = 0, writes = 0;
  int rounds = 1;
  bool enabled = false;
  @override
  Future<Json> readCollaborationEnabled() async =>
      {'instance_id': 'fixture', 'revision': revision, 'enabled': enabled};
  @override
  Future<Json> saveCollaborationEnabled(Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    writes++;
    revision++;
    enabled = update['enabled'] as bool;
    return readCollaborationEnabled();
  }

  @override
  Future<Json> readRoundLimit() async =>
      {'instance_id': 'fixture', 'revision': revision, 'max_rounds': rounds};
  @override
  Future<Json> saveRoundLimit(Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    writes++;
    revision++;
    rounds = update['max_rounds'] as int;
    return readRoundLimit();
  }

  Completer<Json>? pendingRead;
  @override
  Future<Json> readContextLimit() async => pendingRead != null
      ? await pendingRead!.future
      : {
          'instance_id': 'fixture',
          'revision': revision,
          'max_context_chars': value
        };
  @override
  Future<Json> saveContextLimit(Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    writes++;
    revision++;
    value = update['max_context_chars'] as int;
    return readContextLimit();
  }
}

void main() {
  testWidgets('collaboration changes only when explicitly saved',
      (tester) async {
    final api = ContextFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () => showDialog<void>(
                    context: context,
                    builder: (_) => CollaborationSettingDialog(
                        session: session, enablement: true)),
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    expect(find.textContaining('may allow automatic consultations'),
        findsOneWidget);
    await tester.tap(find.byType(SwitchListTile));
    await tester.pump();
    expect(api.enabled, false);
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(api.enabled, true);
    expect(api.writes, 1);
  });
  testWidgets('round limit allows zero and rejects values over 32',
      (tester) async {
    final api = ContextFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () => showDialog<void>(
                    context: context,
                    builder: (_) => CollaborationSettingDialog(
                        session: session, rounds: true)),
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    expect(find.textContaining('zero prevents consultations'), findsOneWidget);
    final field = find.widgetWithText(TextField, 'Maximum rounds');
    await tester.enterText(field, '33');
    await tester.tap(find.text('Save'));
    await tester.pump();
    expect(api.writes, 0);
    await tester.enterText(field, '0');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(api.rounds, 0);
  });
  testWidgets('context setting is reachable from collaboration settings',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final session = DesktopSession(ContextFixture());
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: SettingsPage(controller: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Models and connections').first);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Edit context limit'));
    await tester.tap(find.text('Edit context limit'));
    await tester.pumpAndSettle();
    expect(find.text('Collaboration context limit'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Edit round limit'));
    await tester.tap(find.text('Edit round limit'));
    await tester.pumpAndSettle();
    expect(find.text('Collaboration round limit'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Edit collaboration availability'));
    await tester.tap(find.text('Edit collaboration availability'));
    await tester.pumpAndSettle();
    expect(find.text('Collaboration availability'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
  });
  test('stale read cannot restore settings after changing connections',
      () async {
    final api = ContextFixture()..pendingRead = Completer<Json>();
    final session = DesktopSession(api);
    await session.start();
    final controller = CollaborationSettingController(session);
    final read = controller.load();
    await session.replace(DemoApi());
    api.pendingRead!.complete(
        {'instance_id': 'fixture', 'revision': 0, 'max_context_chars': 8000});
    await read;
    expect(controller.version, isNull);
    expect(controller.available, isFalse);
    controller.dispose();
    session.dispose();
  });
  testWidgets('validates and saves the collaboration context limit',
      (tester) async {
    final api = ContextFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(MaterialApp(
        home: Builder(
            builder: (context) => TextButton(
                onPressed: () => showDialog<void>(
                    context: context,
                    builder: (_) =>
                        CollaborationSettingDialog(session: session)),
                child: const Text('Open')))));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    final field = find.widgetWithText(TextField, 'Maximum characters');
    expect(tester.widget<TextField>(field).controller!.text, '8000');
    await tester.enterText(field, '0');
    await tester.tap(find.text('Save'));
    await tester.pump();
    expect(api.writes, 0);
    expect(find.textContaining('whole number'), findsOneWidget);
    await tester.enterText(field, '2000');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(api.value, 2000);
    expect(api.writes, 1);
  });
}
