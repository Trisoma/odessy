import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/setup/disconnected_api.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';
import 'component_settings_test.dart' show SavedComponents;
import 'setup_controller_test.dart' show RestoringBackend;
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';

class ObservedCoordinator implements InstalledCoordinatorHandle {
  int starts = 0, stops = 0;
  @override
  Future<LocalApi> start() async {
    starts++;
    throw const CoordinatorStartFailure('Fixture startup failure');
  }

  @override
  Future<void> stop() async {
    stops++;
  }
}

class ObservedApi extends DemoApi {
  int creates = 0, selections = 0, stops = 0, deletes = 0;
  @override
  Future<Session> createSession() {
    creates++;
    return super.createSession();
  }

  @override
  Future<void> select(String sid, String profile) {
    selections++;
    return super.select(sid, profile);
  }

  @override
  Future<void> stop(String sid) {
    stops++;
    return super.stop(sid);
  }

  @override
  Future<void> deleteSession(String sid) {
    deletes++;
    return super.deleteSession(sid);
  }
}

void main() {
  void viewport(WidgetTester t, Size size) {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  testWidgets(
      'everyday navigation groups existing operations without changing session',
      (tester) async {
    viewport(tester, const Size(1280, 900));
    final api = ObservedApi();
    final backend = SavedComponents();
    await tester.pumpWidget(OdessyApp(api: api, setupBackend: backend));
    await tester.pumpAndSettle();
    for (final title in ['Assistant', 'Workspaces', 'Devices', 'Settings']) {
      expect(find.widgetWithText(ListTile, title), findsOneWidget);
    }
    expect(find.widgetWithText(ListTile, 'Monitor'), findsNothing);
    expect(find.widgetWithText(ListTile, 'Install Odessy'), findsNothing);
    expect(find.widgetWithText(ListTile, 'Runtime maintenance'), findsNothing);
    await tester.enterText(find.byKey(const Key('composer')), '/question');
    await tester.pump();
    await tester.tap(find.byTooltip('Send message'));
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    for (final title in ['Devices', 'Workspaces', 'Settings', 'Assistant']) {
      await tester.tap(find.widgetWithText(ListTile, title));
      await tester.pumpAndSettle();
    }
    expect((await api.snapshot('demo')).pending, hasLength(1));
    await tester.scrollUntilVisible(find.text('One detail needed'), 200,
        scrollable: find.descendant(
            of: find.byKey(const Key('conversation-transcript')),
            matching: find.byType(Scrollable)).first);
    expect(find.text('One detail needed'), findsOneWidget);
    expect(api.creates, 1);
    expect(api.selections, 0);
    expect(api.stops, 0);
    expect(api.deletes, 0);
    expect(backend.requests.where((r) => r.parameters['action'] == 'save'),
        isEmpty);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'configuration entry goes to model settings not progressive setup',
      (tester) async {
    viewport(tester, const Size(1280, 900));
    await tester.pumpWidget(
        OdessyApp(api: DisconnectedApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure model'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('component-name')), findsOneWidget);
    expect(find.text('Continue'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'saved configuration can still be started explicitly from settings',
      (tester) async {
    viewport(tester, const Size(1280, 900));
    final child = ObservedCoordinator();
    await tester.pumpWidget(OdessyApp(
        api: DisconnectedApi(),
        setupBackend: RestoringBackend(),
        coordinatorFactory: (_) => child));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure model'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Connect saved configuration').last);
    await tester.tap(find.text('Connect saved configuration').last);
    await tester.pumpAndSettle();
    expect(child.starts, 1);
    expect(find.textContaining('Fixture startup failure'), findsWidgets);
    expect(find.text('DEMO'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('settings navigation and content align at the top with card gaps',
      (tester) async {
    viewport(tester, const Size(1280, 900));
    final session = DesktopSession(DemoApi());
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: SettingsPage(controller: session))));
    await tester.pumpAndSettle();
    final nav = tester
        .getTopLeft(find.byKey(const ValueKey('settings-section-General')))
        .dy;
    final connection = tester.getTopLeft(find.text('Connection')).dy;
    expect(nav, lessThan(180));
    expect(connection, lessThan(190));
    final cards = find.byType(Card);
    expect(cards, findsNWidgets(2));
    expect(
        tester.getTopLeft(cards.at(1)).dy -
            tester.getBottomLeft(cards.first).dy,
        greaterThanOrEqualTo(16));
    await tester.pumpWidget(const SizedBox());
    session.dispose();
  });
}
