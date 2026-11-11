import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/runtime_engine.dart';

/// A service that can describe and select engines (the prompt-12 contract).
class EngineApi extends DemoApi {
  EngineApi({this.questions = false});
  final bool questions;
  final List<String?> requested = [];

  @override
  Future<Json> runtimeEngines() async => {
        'default': 'legacy',
        'engines': ['legacy', 'harness'],
        'known': ['legacy', 'harness'],
      };

  @override
  Future<Session> createSession() => createSessionOn();

  @override
  Future<Session> createSessionOn({String? runtime}) async {
    requested.add(runtime);
    return Session('demo',
        selectedProfile: 'auto',
        engine: EngineInfo(
            name: runtime ?? 'legacy',
            source: runtime == null ? 'default' : 'explicit',
            identity: {
              'version': '0.2.1-rc.2'
            },
            capabilities: {
              'engine': runtime ?? 'legacy',
              'live_text_deltas': true,
              'questions': questions,
            }));
  }
}

/// A service from before runtime selection existed.
class BareApi implements OdessyApi {
  @override
  bool get isDemo => false;
  @override
  String get identity => 'bare';
  @override
  Future<List<String>> profiles() async => ['auto'];
  @override
  Future<Session> createSession() async =>
      Session('demo', selectedProfile: 'auto');
  @override
  Future<Session> snapshot(String sid) async => createSession();
  @override
  Stream<SessionEvent> events(String sid, int after) => const Stream.empty();
  @override
  Future<void> send(String sid, String text) async {}
  @override
  Future<void> select(String sid, String profile) async {}
  @override
  Future<void> stop(String sid) async {}
  @override
  Future<void> approve(String sid, String id, bool approved) async {}
  @override
  Future<void> answer(String sid, String id, String answer) async {}
  @override
  Future<Json> settings() async => {};
  @override
  Future<Json> saveSettings(Json value) async => {};
  @override
  Future<void> deleteSession(String sid) async {}
  @override
  void close() {}
}

void main() {
  test('a session reports its backend and the service its engines', () async {
    final api = EngineApi();
    final controller = DesktopSession(api);
    await controller.start();
    expect(controller.engine?.name, 'legacy');
    expect(controller.engine?.source, 'default');
    expect(controller.availableEngines, ['legacy', 'harness']);
    expect(controller.engineDefault, 'legacy');
    controller.dispose();
  });

  test('the chosen engine applies to the next session only', () async {
    final api = EngineApi();
    final controller = DesktopSession(api);
    await controller.start();
    controller.selectNewSessionRuntime('harness');
    // Choosing does not touch the open session.
    expect(controller.session?.engine?.name, 'legacy');
    await controller.newSession();
    expect(api.requested, [null, 'harness']);
    expect(controller.session?.engine?.name, 'harness');
    expect(controller.session?.engine?.source, 'explicit');
    controller.dispose();
  });

  test(
      'requesting an engine from a service that cannot select one fails loudly',
      () async {
    final controller = DesktopSession(BareApi());
    controller.selectNewSessionRuntime('harness');
    await controller.start();
    expect(controller.connected, isFalse);
    expect(controller.error, contains('cannot select a runtime engine'));
    controller.dispose();
  });

  testWidgets(
      'the settings card shows the backend and its unavailable capabilities',
      (tester) async {
    final api = EngineApi(questions: false);
    final controller = DesktopSession(api);
    await controller.start();
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: RuntimeEngine(session: controller))));
    await tester.pumpAndSettle();
    expect(find.text('Runtime engine'), findsOneWidget);
    expect(find.text('This session: legacy'), findsOneWidget);
    await tester.tap(find.text('Runtime engine'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Current backend: legacy'), findsOneWidget);
    expect(find.textContaining('Runtime version: 0.2.1-rc.2'), findsOneWidget);
    expect(find.textContaining('Not available in this session: questions'),
        findsOneWidget);
    controller.dispose();
  });

  testWidgets('selecting an engine updates the next-session choice only',
      (tester) async {
    final api = EngineApi();
    final controller = DesktopSession(api);
    await controller.start();
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: RuntimeEngine(session: controller))));
    await tester.tap(find.text('Runtime engine'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('runtime-engine-select')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('harness (experimental)').last);
    await tester.pumpAndSettle();
    expect(controller.newSessionRuntime, 'harness');
    expect(controller.session?.engine?.name, 'legacy');
    controller.dispose();
  });
}
