import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/devices/presentation/devices.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_client/odessy_client.dart';
import 'component_settings_test.dart' show SavedComponents;

void main() {
  testWidgets('malformed history from a fake is shown as unavailable',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
        OdessyApp(api: MalformedHistoryApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    expect(find.textContaining('Usage history is unavailable'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
  testWidgets('session cost remains available when history endpoint fails',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = FailingHistoryApi();
    final event = SessionEvent(1, 'cost', {
      'total': {'calls': 1, 'prompt': 27, 'cached': 3, 'completion': 5}
    });
    api.ledger = [event];
    await tester
        .pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    expect(find.textContaining('Usage history is unavailable'), findsOneWidget);
    await tester.ensureVisible(find.text('Activity & provenance'));
    await tester.tap(find.text('Activity & provenance'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('API usage and estimated cost'));
    await tester.tap(find.text('API usage and estimated cost'));
    await tester.pumpAndSettle();
    expect(find.text('Input tokens: 27'), findsOneWidget);
  });
  testWidgets('unsent conversation draft survives viewing activity',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester
        .pumpWidget(OdessyApp(api: DemoApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('composer')), 'Unsent draft');
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')).hitTestable());
    await tester.pumpAndSettle();
    expect(find.text('Unsent draft'), findsOneWidget);
  });
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
            of: find.byType(OdessyApp),
            matching: find
                .byKey(const ValueKey('nav-Activity & usage'))
                .hitTestable()),
        findsOneWidget);
    expect(
        find.descendant(
            of: find.byType(ConversationPage),
            matching: find.text('Activity & provenance')),
        findsOneWidget);
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    expect(find.text('API usage and estimated cost'), findsOneWidget);
    expect(find.text('27'), findsOneWidget);
    expect(find.text('Not reported'), findsWidgets);
    expect(find.byType(ConversationPage), findsNothing);
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')).hitTestable());
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
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
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
    await tester.tap(find.byKey(const ValueKey('nav-Devices')).hitTestable());
    await tester.pumpAndSettle();
    await tester.tap(find.descendant(
        of: find.byKey(const ValueKey('host-card-host_demo_jetson')),
        matching: find.widgetWithText(TextButton, 'View details')));
    await tester.pumpAndSettle();
    expect(find.textContaining('does not change'), findsWidgets);
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

class FailingHistoryApi extends ActivityEventApi implements UsageHistoryApi {
  @override
  Future<Json> usageHistory(
          {int days = 30,
          String? provider,
          String? model,
          int limit = 50,
          int offset = 0}) async =>
      throw const ApiFailure(404);
}

class MalformedHistoryApi extends FailingHistoryApi {
  @override
  Future<Json> usageHistory(
          {int days = 30,
          String? provider,
          String? model,
          int limit = 50,
          int offset = 0}) async =>
      {
        'schema_version': 1,
        'summary': {'requests': 'bad'},
        'daily': [],
        'groups': [],
        'providers': [],
        'requests': [],
        'options': [],
        'total': 0,
        'limit': limit,
        'offset': offset,
      };
}
