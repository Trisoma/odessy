import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/devices/presentation/devices.dart';
import 'package:odessy_desktop/features/shell/presentation/device_sidebar.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_client/odessy_client.dart';
import 'component_settings_test.dart' show SavedComponents;

void main() {
  testWidgets('activity button opens usage page and preserves conversation',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = ActivityEventApi();
    await tester
        .pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    final cost = SessionEvent(1, 'cost', {
      'total': {'calls': 1, 'prompt': 27, 'cached': 3, 'completion': 5},
      'total_cost': null,
    });
    api.ledger = [cost];
    api.eventsController.add(cost);
    await tester.pumpAndSettle();
    expect(
        find.descendant(
            of: find.byType(DeviceSidebar),
            matching: find.text('Activity & usage')),
        findsOneWidget);
    expect(
        find.descendant(
            of: find.byType(ConversationPage),
            matching: find.text('Activity & provenance')),
        findsNothing);
    await tester.tap(find.text('Activity & usage'));
    await tester.pumpAndSettle();
    expect(find.text('API usage and estimated cost'), findsOneWidget);
    expect(find.text('27'), findsOneWidget);
    expect(find.text('Not reported'), findsWidgets);
    expect(find.byType(ConversationPage), findsNothing);
    await tester.tap(find.text('Assistant'));
    await tester.pumpAndSettle();
    expect(find.byType(ConversationPage), findsOneWidget);
  });

  testWidgets('activity stays accessible in the narrow navigation drawer',
      (tester) async {
    tester.view.physicalSize = const Size(800, 700);
    tester.view.devicePixelRatio = 1;
    tester.platformDispatcher.textScaleFactorTestValue = 1.5;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final api = ActivityEventApi();
    await tester
        .pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    final cost = SessionEvent(1, 'cost', {
      'total': {'calls': 1, 'prompt': 27, 'cached': 3, 'completion': 5},
      'total_cost': null,
    });
    api.ledger = [cost];
    api.eventsController.add(cost);
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Open navigation menu'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Activity & usage'));
    await tester.pumpAndSettle();
    expect(find.text('27'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('model failure is shown once and opens model settings',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = ErrorEventApi();
    await tester
        .pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    api.eventsController.add(SessionEvent(1, 'error',
        {'message': 'Ollama LFM API call failed: [WinError 10061] 连接被拒绝'}));
    await tester.pumpAndSettle();
    expect(find.text('Session needs attention'), findsOneWidget);
    expect(
        find.text(
            'Cannot connect to Ollama. Start the model service or choose another model in Settings.'),
        findsOneWidget);
    expect(find.textContaining('连接被拒绝'), findsNothing);
    await tester.tap(find.text('Open model settings'));
    await tester.pumpAndSettle();
    expect(find.text('Models and connections'), findsWidgets);
    expect(find.textContaining('连接被拒绝'), findsNothing);
  });

  testWidgets('desktop sends, stops and opens device settings', (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(api: DemoApi()));
    await tester.pumpAndSettle();
    expect(find.text('DEMO'), findsWidgets);
    await tester.enterText(find.byKey(const Key('composer')), 'Hello Odessy');
    await tester.pump();
    await tester.tap(find.byTooltip('Send message'));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Hello Odessy'), findsOneWidget);
    await tester.tap(find.byTooltip('Stop current turn'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Stopped'), findsWidgets);
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    expect(find.textContaining('does not change'), findsOneWidget);
    expect(find.text('Telemetry access'), findsOneWidget);
    expect(find.text('Allowed'), findsOneWidget);
    await tester.tap(find.descendant(
        of: find.byType(DevicePage), matching: find.text('Settings')));
    await tester.pumpAndSettle();
    expect(find.text('Device configuration is unavailable.'), findsOneWidget);
  });
  testWidgets('approval is explicit and distinct from a question',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
        OdessyApp(api: DemoApi(delay: const Duration(milliseconds: 20))));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('composer')), '/approval');
    await tester.pump();
    await tester.tap(find.byTooltip('Send message'));
    await tester.pumpAndSettle();
    expect(find.text('Approve once'), findsOneWidget);
    expect(find.text('Deny'), findsOneWidget);
    final deny = find.widgetWithText(OutlinedButton, 'Deny');
    await tester.ensureVisible(deny);
    await tester.pumpAndSettle();
    await tester.tap(deny);
    await tester.pumpAndSettle();
    expect(find.text('Approve once'), findsNothing);
  });
}

class ErrorEventApi extends DemoApi {
  final eventsController = StreamController<SessionEvent>.broadcast();

  @override
  Stream<SessionEvent> events(String sid, int after) => eventsController.stream;

  @override
  void close() {
    unawaited(eventsController.close());
    super.close();
  }
}

class ActivityEventApi extends ErrorEventApi {
  List<SessionEvent> ledger = [];
  @override
  Future<Session> snapshot(String sid) async =>
      Session(sid, selectedProfile: 'auto', events: ledger);
}
