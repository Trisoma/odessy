import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/conversation/controllers/voice_controller.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation_voice_controls.dart';

/// Records the wire-level voice actions. Nothing here touches a microphone.
class VoiceApi extends DemoApi implements DesktopVoiceApi {
  VoiceApi(this.sessionId) : super(delay: Duration.zero);
  final String sessionId;
  final commands = <({String action, String? capture})>[];
  final stateCalls = <String>[];
  final snapshots = <String>[];
  Completer<VoiceSnapshot>? pendingState, pendingStart;
  List<SessionEvent> sessionEvents = [];
  String state = 'idle', mode = 'push_to_talk';
  String? capture;
  bool supported = true, fail = false;
  int creates = 0, submissions = 0;

  VoiceSnapshot get value => VoiceSnapshot.fromJson({
        'supported': supported,
        'state': state,
        'mode': mode,
        'capture_id': capture,
        'recorder_host': 'coordinator-test',
        'message': state == 'error' ? 'Audio input unavailable.' : '',
      });

  @override
  Future<List<String>> profiles() async => const ['local'];

  @override
  Future<Session> createSession() async {
    creates++;
    return snapshot(sessionId);
  }

  @override
  Future<Session> snapshot(String sid) async {
    snapshots.add(sid);
    return Session(sid, events: sessionEvents);
  }

  @override
  Future<void> send(String sid, String text) async => submissions++;

  @override
  Future<VoiceSnapshot> voiceState(String sid) {
    stateCalls.add(sid);
    final pending = pendingState;
    return pending == null ? Future.value(value) : pending.future;
  }

  @override
  Future<VoiceSnapshot> voiceCommand(String sid, String action,
      {String? captureId, String? interactionId}) async {
    commands.add((action: action, capture: captureId));
    if (fail) throw const ApiFailure(503);
    if (action == 'start_capture' && pendingStart != null) {
      return pendingStart!.future;
    }
    switch (action) {
      case 'start_capture':
      case 'test_input':
        state = 'recording';
        capture = 'capture-$sid';
      case 'finish_capture':
        state = 'transcribing';
      case 'cancel_capture':
        state = 'idle';
        capture = null;
      case 'enable_wake':
        mode = 'wake';
        state = 'listening';
      case 'disable_wake':
        mode = 'push_to_talk';
        state = 'idle';
      case 'test_output':
        state = 'speaking';
      case 'stop_playback':
        state = 'idle';
    }
    return value;
  }
}

Future<DesktopSession> bound(VoiceApi api) async {
  final session = DesktopSession(DemoApi(delay: Duration.zero));
  await session.replace(api);
  return session;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('construction reads state but never starts capture or playback',
      () async {
    final api = VoiceApi('s1');
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();
    expect(api.stateCalls, isNotEmpty);
    expect(api.commands, isEmpty);
    expect(voice.state, 'idle');
    expect(voice.recording, isFalse);
    expect(session.session!.messages, isEmpty);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('explicit push-to-talk lifecycle uses the current capture id', () async {
    final api = VoiceApi('s1');
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();

    await voice.command('start_capture');
    expect(voice.recording, isTrue);
    expect(voice.hasCapture, isTrue);
    expect(voice.canFinishCapture, isTrue);
    expect(voice.canStartCapture, isFalse);

    await voice.command('finish_capture');
    expect(
        api.commands.map((c) => c.action), ['start_capture', 'finish_capture']);
    expect(api.commands.last.capture, 'capture-s1');
    // The coordinator admitted the turn; this controller sent no text.
    expect(api.submissions, 0);

    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('cancelling a capture releases it without submitting text', () async {
    final api = VoiceApi('s1');
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();
    await voice.command('start_capture');
    await voice.command('cancel_capture');
    expect(
        api.commands.last, (action: 'cancel_capture', capture: 'capture-s1'));
    expect(voice.recording, isFalse);
    expect(api.submissions, 0);
    expect(session.session!.messages, isEmpty);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('a transcript event is attached to this session and not resent',
      () async {
    final api = VoiceApi('s1');
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();
    api.sessionEvents = [
      SessionEvent(1, 'voice_transcript', {
        'text': 'open the collector notes',
        'capture_id': 'c1',
      })
    ];
    await session.refresh();
    expect(voice.transcript?.text, 'open the collector notes');
    expect(voice.transcript?.sessionId, 's1');
    expect(voice.transcript?.captureId, 'c1');
    // Displaying it must never add a second submission.
    expect(api.submissions, 0);
    expect(session.session!.messages, isEmpty);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('failure keeps text available and reports an error', () async {
    final api = VoiceApi('s1');
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();
    api.fail = true;
    await voice.command('start_capture');
    expect(voice.error, isNotEmpty);
    expect(session.connected, isTrue);
    expect(api.submissions, 0);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('changing target releases the previous owner and ignores its late reply',
      () async {
    final first = VoiceApi('one');
    final session = await bound(first);
    final voice = VoiceController(session);
    await voice.refresh();
    await voice.command('start_capture');
    expect(voice.recording, isTrue);

    // A reply that arrives after the target moved on must not resurrect it.
    final late = Completer<VoiceSnapshot>();
    first.pendingState = late;
    final second = VoiceApi('two');
    await session.replace(second);
    await voice.refresh();

    late.complete(VoiceSnapshot.fromJson({
      'supported': true,
      'state': 'recording',
      'capture_id': 'stale-capture',
    }));
    await Future<void>.delayed(Duration.zero);

    expect(voice.recording, isFalse);
    expect(voice.captureId, isNot('stale-capture'));
    // The old capture was actually released rather than orphaned.
    expect(first.commands.map((c) => c.action), contains('cancel_capture'));
    expect(first.commands.last.capture, 'capture-one');

    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('release cancels a capture whose start response arrives late', () async {
    final api = VoiceApi('s1')..pendingStart = Completer<VoiceSnapshot>();
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();
    final start = voice.command('start_capture');
    final release = voice.release();
    api.state = 'recording';
    api.capture = 'capture-s1';
    api.pendingStart!.complete(api.value);
    await Future.wait([start, release]);
    expect(
        api.commands.last, (action: 'cancel_capture', capture: 'capture-s1'));
    expect(api.state, 'idle');
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  test('explicit release stops wake listening on the owning target', () async {
    final api = VoiceApi('s1');
    final session = await bound(api);
    final voice = VoiceController(session);
    await voice.refresh();
    await voice.command('enable_wake');
    expect(voice.wakeMode, isTrue);
    await voice.release();
    expect(api.commands.last, (action: 'disable_wake', capture: null));
    expect(voice.snapshot, isNull);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });

  testWidgets('composer controls expose every voice option without capturing',
      (tester) async {
    final api = VoiceApi('s1');
    late DesktopSession session;
    late VoiceController voice;
    await tester.runAsync(() async {
      session = await bound(api);
      voice = VoiceController(session);
      await voice.refresh();
    });
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
          body: ConversationVoiceControls(controller: voice),
        )));
    await tester.pumpAndSettle();

    expect(api.commands, isEmpty);
    expect(find.byKey(const ValueKey('voice-status')), findsOneWidget);
    expect(find.textContaining('microphone is not accessed'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('voice-mic')));
    await tester.pumpAndSettle();
    expect(api.commands.single.action, 'start_capture');

    await tester.tap(find.byKey(const ValueKey('voice-options')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('voice-check')), findsOneWidget);
    expect(find.byKey(const ValueKey('voice-wake-on')), findsOneWidget);
    expect(find.byKey(const ValueKey('voice-test-input')), findsOneWidget);
    expect(find.byKey(const ValueKey('voice-test-output')), findsOneWidget);
    expect(find.byKey(const ValueKey('voice-stop-playback-option')),
        findsOneWidget);

    // Still only the explicit start: opening options captures nothing.
    expect(api.commands.map((c) => c.action), ['start_capture']);

    await tester.runAsync(() async {
      voice.dispose();
      await session.shutdown();
      session.dispose();
    });
  });

  testWidgets('unavailable voice keeps typed input usable and disables the mic',
      (tester) async {
    final api = VoiceApi('s1')..supported = false;
    late DesktopSession session;
    late VoiceController voice;
    await tester.runAsync(() async {
      session = await bound(api);
      voice = VoiceController(session);
      await voice.refresh();
    });
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(body: ConversationVoiceControls(controller: voice))));
    await tester.pumpAndSettle();
    final mic =
        tester.widget<IconButton>(find.byKey(const ValueKey('voice-mic')));
    expect(mic.onPressed, isNull);
    expect(find.textContaining('typed input remains available'), findsWidgets);
    expect(api.commands, isEmpty);
    await tester.runAsync(() async {
      voice.dispose();
      await session.shutdown();
      session.dispose();
    });
  });
}
