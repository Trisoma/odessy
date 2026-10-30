import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'conversation_presentation_test.dart';

class VoiceFixture extends ConversationFixtureApi implements VoiceApi {
  int starts = 0, stops = 0, heartbeats = 0;
  bool failStart = false, failStop = false;
  Completer<Json>? heartbeatGate;
  Json state(String state) => {
        'state': state,
        'input_available': state == 'active',
        'output_available': state == 'active'
      };
  @override
  Future<Json> startVoice(String sid) async {
    starts++;
    if (failStart) throw const ApiFailure(503);
    return state('starting');
  }

  @override
  Future<Json> heartbeatVoice(String sid) async {
    heartbeats++;
    return heartbeatGate?.future ?? state('active');
  }

  @override
  Future<Json> stopVoice(String sid) async {
    stops++;
    if (failStop) throw const ApiFailure(503);
    return state('off');
  }

  @override
  Future<Json> voiceStatus(String sid) async => state('off');
}

void main() {
  testWidgets(
      'voice errors identify the failed stage without exposing diagnostics',
      (tester) async {
    final api = VoiceFixture();
    final session = DesktopSession(api);
    addTearDown(session.dispose);
    await session.start();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ConversationPage(controller: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Start voice input'));
    await tester.pump();
    api.activity = [
      SessionEvent(
          1, 'voice_error', {'stage': 'capture', 'message': 'private path'})
    ];
    await session.refresh();
    await tester.pump();
    expect(find.textContaining('Microphone capture failed'), findsOneWidget);
    expect(find.textContaining('private path'), findsNothing);
    await tester.tap(find.byTooltip('Stop voice input'));
    await tester.pump();
    expect(find.textContaining('Microphone capture failed'), findsNothing);
    await tester.tap(find.byTooltip('Start voice input'));
    await tester.pump();
    expect(find.textContaining('Microphone capture failed'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });
  testWidgets('late heartbeat cannot reactivate voice after stop',
      (tester) async {
    final api = VoiceFixture()..heartbeatGate = Completer<Json>();
    final session = DesktopSession(api);
    addTearDown(session.dispose);
    await session.start();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ConversationPage(controller: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Start voice input'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 5));
    expect(api.heartbeats, 1);
    await tester.tap(find.byTooltip('Stop voice input'));
    await tester.pump();
    api.heartbeatGate!.complete(api.state('active'));
    await tester.pump();
    expect(find.byTooltip('Start voice input'), findsOneWidget);
    expect(find.text('Microphone and voice output active on the local service'),
        findsNothing);
    expect(api.stops, 1);
  });

  testWidgets('voice requires an explicit start and stops when leaving',
      (tester) async {
    final api = VoiceFixture();
    final session = DesktopSession(api);
    addTearDown(session.dispose);
    await session.start();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ConversationPage(controller: session))));
    await tester.pumpAndSettle();
    expect(api.starts, 0);
    await tester.tap(find.byTooltip('Start voice input'));
    await tester.pump();
    expect(api.starts, 1);
    expect(find.text('Starting voice on the local service…'), findsOneWidget);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(api.heartbeats, 1);
    expect(find.text('Microphone and voice output active on the local service'),
        findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    expect(api.stops, 1);
  });

  testWidgets('unconfirmed start never automatically retries', (tester) async {
    final api = VoiceFixture()
      ..failStart = true
      ..failStop = true;
    final session = DesktopSession(api);
    addTearDown(session.dispose);
    await session.start();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ConversationPage(controller: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Start voice input'));
    await tester.pump();
    expect(api.starts, 1);
    expect(find.byTooltip('Stop voice input'), findsOneWidget);
    expect(
        find.textContaining('Voice start was not confirmed'), findsOneWidget);
    await tester.pump(const Duration(seconds: 10));
    expect(api.starts, 1);
    expect(api.heartbeats, 0);
  });
}
