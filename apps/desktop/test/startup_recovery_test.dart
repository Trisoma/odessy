import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'component_settings_test.dart' show SavedComponents;
import 'setup_controller_test.dart' show RestoringBackend;
import 'package:odessy_desktop/app/setup/disconnected_api.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';

class RecoveryApi extends LocalApi {
  RecoveryApi() : super(Uri.parse('http://127.0.0.1:9'), 'fixture-token');
  @override
  Future<List<String>> profiles() async => ['current'];
  @override
  Future<List<Profile>> profileMetadata() async => [];
  @override
  Future<RuntimeCapabilities> runtimeCapabilities() async => throw const ApiFailure(404);
  @override
  Future<Session> createSession() async => throw const ApiFailure(503);
}

class RecoveryCoordinator implements InstalledCoordinatorHandle {
  int stops = 0;
  @override
  Future<LocalApi> start() async => RecoveryApi();
  @override
  Future<void> stop() async { stops++; }
}

class UnavailableCoordinatorApi extends DemoApi {
  int creates = 0;
  @override
  Future<Session> createSession() async {
    creates++;
    throw const ApiFailure(503);
  }
}

class ChatOnlyApi extends DemoApi {
  int sends = 0;
  @override
  Future<Session> createSession() async {
    final session = await super.createSession();
    return Session(session.id, selectedProfile: session.selectedProfile, events: [
      SessionEvent(1, 'execution_unavailable', {'message': 'fixture'})
    ]);
  }
  @override
  Future<void> send(String sid, String text, {String? participant}) {
    sends++;
    return super.send(sid, text);
  }
}

void main() {
  testWidgets('chat-only warning does not disable text conversation', (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = ChatOnlyApi();
    await tester.pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    expect(find.textContaining('Chat-only:'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('composer')), 'Hi');
    await tester.tap(find.byTooltip('Send message'));
    await tester.pumpAndSettle();
    expect(api.sends, 1);
    expect(find.textContaining('Chat-only:'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('a ready service survives unavailable conversation initialization', (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final child = RecoveryCoordinator();
    await tester.pumpWidget(OdessyApp(api: DisconnectedApi(), setupBackend: RestoringBackend(), coordinatorFactory: (_) => child));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure model'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Connect saved configuration').last);
    await tester.tap(find.text('Connect saved configuration').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('Cannot start a conversation'), findsWidgets);
    expect(child.stops, 0, reason: 'independent service management must remain available');
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('startup failure offers settings and devices without retrying work', (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = UnavailableCoordinatorApi();
    await tester.pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    expect(find.textContaining('Cannot start a conversation'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('startup-recovery-settings')));
    await tester.pumpAndSettle();
    expect(find.text('Preferences'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('startup-recovery-devices')));
    await tester.pumpAndSettle();
    expect(find.text('Preferences'), findsNothing);
    expect(api.creates, 1);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
