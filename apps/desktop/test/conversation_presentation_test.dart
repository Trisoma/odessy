import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_desktop/features/conversation/presentation/interaction_card.dart';
import 'package:odessy_desktop/features/conversation/presentation/session_activity.dart';
import 'package:odessy_desktop/features/conversation/controllers/voice_controller.dart';
import 'voice_controller_test.dart' show AudioApi;

Json interactionFixture(
        {String kind = 'approval',
        double? expiry,
        List<String> channels = const ['authenticated_ui']}) =>
    {
      'id': 'request-1',
      'kind': kind,
      'prompt': 'Untrusted summary',
      'authoritative_prompt': 'Allow this exact workspace inspection?',
      'question': 'Which workspace should I inspect?',
      'tool': 'terminal.run',
      'host': 'Demo workstation',
      'arguments': {'command': 'git diff --stat'},
      'permission_scopes': ['workspace.read'],
      'risk': 'medium',
      'allowed_channels': channels,
      'expires_at':
          expiry ?? DateTime.now().millisecondsSinceEpoch / 1000 + 600,
      'choices': ['Current workspace', 'Another workspace'],
      'allow_free_text': true,
    };

/// Test/screenshot fixture only. Never substituted into a failed live session.
class ConversationFixtureApi extends DemoApi {
  List<ChatMessage> messages = [];
  List<Json> pending = [];
  List<SessionEvent> activity = [];
  bool running = false, failSend = false;
  int sends = 0, approvals = 0, answers = 0;
  bool? approved;
  String? response, requestId, responseSession;
  Completer<void>? sendGate;
  @override
  Future<Session> snapshot(String sid) async => Session('demo',
      messages: messages,
      pending: pending,
      events: activity,
      running: running,
      selectedProfile: 'auto',
      activeProfile: running ? 'local' : null);
  @override
  Future<void> send(String sid, String text) async {
    sends++;
    await sendGate?.future;
    if (failSend) throw const ApiFailure(500);
    messages = [...messages, ChatMessage('user', text)];
  }

  @override
  Future<void> approve(String sid, String id, bool value) async {
    approvals++;
    approved = value;
    requestId = id;
    responseSession = sid;
    pending = [];
  }

  @override
  Future<void> answer(String sid, String id, String value) async {
    answers++;
    response = value;
    requestId = id;
    responseSession = sid;
    pending = [];
  }
}

Future<void> mountConversation(WidgetTester tester, DesktopSession c,
    {Size size = const Size(1100, 1000),
    double scale = 1,
    Brightness brightness = Brightness.dark,
    Widget? body}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: odessyTheme(brightness),
    home: MediaQuery(
        data: MediaQueryData(size: size, textScaler: TextScaler.linear(scale)),
        child: Scaffold(body: body ?? ConversationPage(controller: c))),
  ));
  await tester.pump();
}

void main() {
  testWidgets('assistant lists are readable while full copy keeps source',
      (tester) async {
    const source = 'Tasks:\n- First item\n* Second item\n2. Third item';
    final api = ConversationFixtureApi()
      ..messages = [const ChatMessage('assistant', source)];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'];
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await mountConversation(tester, c);
    await tester.pumpAndSettle();
    expect(find.text('Tasks:\n• First item\n• Second item\n2. Third item'),
        findsOneWidget);
    await tester.tap(find.byTooltip('Copy message'));
    await tester.pumpAndSettle();
    expect(copied, source);
  });

  testWidgets('assistant code blocks copy exact code and preserve message copy',
      (tester) async {
    const source = 'Example <script>:\n'
        '\x60\x60\x60dart\nfinal value = 42;\n\x60\x60\x60\n'
        'More text\n~~~~text\nhello\n~~~~\n'
        'Unfinished:\n\x60\x60\x60python\nprint(1)';
    final api = ConversationFixtureApi()
      ..messages = [const ChatMessage('assistant', source)];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'];
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await mountConversation(tester, c);
    await tester.pumpAndSettle();
    expect(find.text('Example <script>:\n'), findsOneWidget);
    expect(find.text('dart'), findsOneWidget);
    expect(find.text('text'), findsOneWidget);
    expect(find.textContaining('Unfinished:'), findsOneWidget);
    expect(find.byTooltip('Copy code'), findsNWidgets(2));
    await tester.tap(find.byTooltip('Copy code').first);
    await tester.pumpAndSettle();
    expect(copied, 'final value = 42;\n');
    await tester.tap(find.byTooltip('Copy message'));
    await tester.pumpAndSettle();
    expect(copied, source);
  });

  testWidgets('long code lines remain accessible in a narrow window',
      (tester) async {
    final code = 'x' * 300;
    final source = '\x60\x60\x60\n$code\n\x60\x60\x60';
    final api = ConversationFixtureApi()
      ..messages = [ChatMessage('assistant', source)];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c, size: const Size(360, 900), scale: 1.3);
    await tester.pumpAndSettle();
    expect(find.text('Code'), findsOneWidget);
    expect(find.text('$code\n'), findsOneWidget);
    expect(find.byTooltip('Copy code').hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('unclosed fences remain literal and shorter fences stay in code',
      (tester) async {
    const open = 'A partial response:\n\x60\x60\x60dart\nprint(1)';
    const fenced =
        '\x60\x60\x60\x60text\nline one\n\x60\x60\x60\nline two\n\x60\x60\x60\x60';
    final api = ConversationFixtureApi()
      ..messages = [
        const ChatMessage('assistant', open),
        const ChatMessage('assistant', fenced)
      ];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    await tester.pumpAndSettle();
    expect(find.text(open), findsOneWidget);
    expect(find.text('line one\n\x60\x60\x60\nline two\n'), findsOneWidget);
    expect(find.byTooltip('Copy code'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('session details are available on demand', (tester) async {
    final c = DesktopSession(ConversationFixtureApi());
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    expect(find.textContaining('Coordinator:'), findsNothing);
    await tester.tap(find.byTooltip('Session details'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Coordinator:'), findsOneWidget);
    await tester.tap(find.text('Close'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Coordinator:'), findsNothing);
  });

  testWidgets('voice options stay accessible and active playback can stop',
      (tester) async {
    final api = AudioApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    final voice = VoiceController(c);
    addTearDown(voice.dispose);
    await voice.refresh();
    await mountConversation(tester, c,
        body: ConversationPage(controller: c, voice: voice));
    expect(find.text('Check voice availability'), findsNothing);
    await tester.tap(find.text('Voice options'));
    await tester.pump();
    expect(find.text('Check voice availability'), findsOneWidget);
    expect(find.text('Test speech output'), findsOneWidget);
    await tester.tap(find.text('Voice options'));
    await tester.pump();
    api.state = 'speaking';
    await voice.refresh();
    await tester.pump();
    expect(find.text('Audio: speaking'), findsOneWidget);
    expect(find.text('Stop playback'), findsOneWidget);
    await tester.tap(find.text('Stop playback'));
    await tester.pumpAndSettle();
    expect(api.commands, contains('stop_playback'));
    expect(tester.takeException(), isNull);
  });

  testWidgets('wake listening can stop while voice options are closed',
      (tester) async {
    final api = AudioApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    final voice = VoiceController(c);
    addTearDown(voice.dispose);
    await voice.refresh();
    voice.snapshot = VoiceSnapshot.fromJson({
      'supported': true,
      'state': 'idle',
      'mode': 'wake',
    });
    await mountConversation(tester, c,
        body: ConversationPage(controller: c, voice: voice));
    expect(find.text('Check voice availability'), findsNothing);
    expect(find.text('Wake listening'), findsOneWidget);
    expect(find.text('Stop wake listening'), findsOneWidget);
    await tester.tap(find.text('Stop wake listening'));
    await tester.pumpAndSettle();
    expect(api.commands, contains('disable_wake'));
  });

  testWidgets('recording status and cancel remain visible with options closed',
      (tester) async {
    final api = AudioApi()..state = 'recording';
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    final voice = VoiceController(c);
    addTearDown(voice.dispose);
    await voice.refresh();
    await mountConversation(tester, c,
        body: ConversationPage(controller: c, voice: voice));
    expect(find.text('Audio: recording'), findsOneWidget);
    expect(
        find.byTooltip('Finish recording and submit speech'), findsOneWidget);
    await tester.tap(find.byTooltip('Cancel recording without submitting'));
    await tester.pumpAndSettle();
    expect(api.commands, contains('cancel_capture'));
  });

  for (final (error, expected) in [
    (
      'Ollama LFM API call failed: <urlopen error [WinError 10061] 连接被拒绝>',
      'Cannot connect to Ollama. Start the model service or choose another model in Settings.'
    ),
    (
      'Ollama API call failed (429): usage limit reached',
      'The model service reported a usage limit. Check the provider account or choose another model.'
    ),
    (
      'Ollama API call failed (404): model not found',
      'The selected model is unavailable. Check the model name and configure an available model in Settings.'
    ),
    (
      'Stop was not confirmed. Odessy service returned HTTP 500.',
      'Stop was not confirmed. Odessy service returned HTTP 500.'
    ),
  ]) {
    testWidgets('session notice retains actionable information: $error',
        (tester) async {
      final c = DesktopSession(ConversationFixtureApi());
      addTearDown(c.dispose);
      await c.start();
      c.error = error;
      var openedModels = false;
      await mountConversation(tester, c,
          body: ConversationPage(
              controller: c, onOpenModels: () => openedModels = true));
      expect(find.text(expected), findsOneWidget);
      if (expected != error) {
        expect(find.text(error), findsNothing);
        await tester.tap(find.text('Open model settings'));
        expect(openedModels, isTrue);
      } else {
        expect(find.text('Open model settings'), findsNothing);
      }
    });
  }

  for (final size in [const Size(1100, 1000), const Size(360, 900)]) {
    testWidgets('user messages fit their content and stay usable at $size',
        (tester) async {
      final api = ConversationFixtureApi()
        ..messages = [
          const ChatMessage('user', 'Hello'),
          ChatMessage('user', 'A long message with a URL: ${'x' * 200}'),
          const ChatMessage('assistant', 'Here is the response.'),
        ];
      final c = DesktopSession(api);
      addTearDown(c.dispose);
      await c.start();
      await mountConversation(tester, c, size: size);
      await tester.pumpAndSettle();
      final bubbles = find.byKey(const Key('user-message-bubble'));
      expect(bubbles, findsNWidgets(2));
      final short = tester.getRect(bubbles.first);
      final long = tester.getRect(bubbles.last);
      expect(short.width, lessThan(long.width));
      expect(long.right, lessThanOrEqualTo(size.width));
      expect(short.right, closeTo(long.right, 1));
      expect(
          find.descendant(
              of: bubbles, matching: find.byTooltip('Copy message')),
          findsNWidgets(2));
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('examples fill the draft and blank drafts cannot submit',
      (tester) async {
    final api = ConversationFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    expect(
        tester
            .widget<IconButton>(find.byKey(const Key('send-or-stop')))
            .onPressed,
        isNull);
    await tester.tap(find.text('Plan my next task'));
    await tester.pump();
    expect(api.sends, 0);
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('composer')))
            .controller!
            .text,
        'Plan my next task');
  });

  testWidgets(
      'Enter submits once, Shift+Enter adds a line, composition cannot submit',
      (tester) async {
    final api = ConversationFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    final composer = find.byKey(const Key('composer'));
    await tester.enterText(composer, 'First line');
    await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
    await tester.pump();
    expect(tester.widget<TextField>(composer).controller!.text, 'First line\n');
    expect(api.sends, 0);
    tester.testTextInput.updateEditingValue(const TextEditingValue(
        text: 'pending',
        selection: TextSelection.collapsed(offset: 7),
        composing: TextRange(start: 0, end: 7)));
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(api.sends, 0);
    tester.testTextInput.updateEditingValue(const TextEditingValue(
        text: 'Ready to send', selection: TextSelection.collapsed(offset: 13)));
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(api.sends, 1);
    expect(api.messages.single.text, 'Ready to send');
    expect(tester.widget<TextField>(composer).controller!.text, isEmpty);
  });

  testWidgets(
      'failed send retains the draft and pending sends disable duplicates',
      (tester) async {
    final api = ConversationFixtureApi()..failSend = true;
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    final composer = find.byKey(const Key('composer'));
    await tester.enterText(composer, 'Keep this draft');
    await tester.pump();
    await tester.tap(find.byTooltip('Send message'));
    await tester.pumpAndSettle();
    expect(
        tester.widget<TextField>(composer).controller!.text, 'Keep this draft');
    expect(find.text('Session needs attention'), findsOneWidget);
    api.failSend = false;
    api.sendGate = Completer<void>();
    await tester.tap(find.byTooltip('Send message'));
    await tester.pump();
    expect(
        tester
            .widget<IconButton>(find.byKey(const Key('send-or-stop')))
            .onPressed,
        isNull);
    api.sendGate!.complete();
    await tester.pumpAndSettle();
    expect(api.sends, 2);
  });

  testWidgets('roles, literal message content and copying preserve source text',
      (tester) async {
    const literal = '<script>example</script>\n```dart\nprint(1);\n```';
    final api = ConversationFixtureApi()
      ..messages = [
        const ChatMessage('tool', literal),
        const ChatMessage('system', 'Service notice')
      ];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'];
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await mountConversation(tester, c);
    expect(find.text('Tool output'), findsOneWidget);
    expect(find.text('System'), findsOneWidget);
    expect(find.text(literal), findsOneWidget);
    await tester.tap(find.byTooltip('Copy message').first);
    await tester.pumpAndSettle();
    expect(copied, literal);
  });

  testWidgets(
      'activity exposes complete payload without inferring tool success',
      (tester) async {
    final api = ConversationFixtureApi();
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    final events = List.generate(
        15, (i) => SessionEvent(i + 1, 'progress', {'message': 'step $i'}));
    events.add(SessionEvent(16, 'result', {
      'tool_request': {
        'name': 'git.diff',
        'arguments': {'path': 'README.md'}
      },
      'tool_result': {'error': 'Permission denied'},
    }));
    await mountConversation(tester, c,
        body: SingleChildScrollView(child: SessionActivity(events: events)));
    await tester.tap(find.text('Activity & provenance'));
    await tester.pumpAndSettle();
    expect(find.text('Latest 12 of 16 available events'), findsOneWidget);
    await tester.tap(find.text('Turn result'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Permission denied'), findsOneWidget);
    expect(find.text('Completed'), findsNothing);
    expect(find.text('Read-only'), findsNothing);
  });

  testWidgets('service cost event is visible from conversation activity',
      (tester) async {
    final api = ConversationFixtureApi()
      ..activity = [
        SessionEvent(1, 'cost', {
          'total': {'calls': 1, 'prompt': 27, 'cached': 3, 'completion': 5},
          'total_cost': null,
        })
      ];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    await tester.tap(find.text('Activity & provenance'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('API usage and estimated cost'));
    await tester.pumpAndSettle();
    expect(find.text('Input tokens: 27'), findsOneWidget);
    expect(find.text('Estimated total: Not reported'), findsOneWidget);
  });

  testWidgets(
      'new messages preserve reading position and latest action follows them',
      (tester) async {
    final api = ConversationFixtureApi()
      ..messages = List.generate(
          35,
          (i) => ChatMessage(
              'assistant', 'Message $i: ${'workspace detail ' * 12}'));
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c, size: const Size(1000, 700));
    final transcript = find.byKey(const Key('conversation-transcript'));
    final position = tester
        .state<ScrollableState>(find
            .descendant(of: transcript, matching: find.byType(Scrollable))
            .first)
        .position;
    position.jumpTo(300);
    await tester.pump();
    final before = position.pixels;
    api.messages = [
      ...api.messages,
      const ChatMessage('assistant', 'A new update')
    ];
    await c.refresh();
    await tester.pumpAndSettle();
    expect(position.pixels, closeTo(before, 1));
    await tester.tap(find.text('Latest messages'));
    await tester.pumpAndSettle();
    expect(position.extentAfter, lessThan(1));
  });

  for (final action in ['Deny', 'Approve once']) {
    testWidgets('$action binds to the displayed authoritative request',
        (tester) async {
      final api = ConversationFixtureApi()..pending = [interactionFixture()];
      final c = DesktopSession(api);
      addTearDown(c.dispose);
      await c.start();
      await mountConversation(tester, c);
      expect(
          find.text('Allow this exact workspace inspection?'), findsOneWidget);
      expect(find.text('Untrusted summary'), findsNothing);
      expect(find.textContaining('git diff --stat'), findsOneWidget);
      final button = find.text(action);
      await tester.ensureVisible(button);
      await tester.tap(button);
      await tester.pumpAndSettle();
      expect(api.approvals, 1);
      expect(api.approved, action == 'Approve once');
      expect(api.requestId, 'request-1');
      expect(api.responseSession, 'demo');
      expect(api.answers, 0);
    });
  }

  for (final mode in [
    'expired',
    'channel',
    'offline',
    'stale',
    'busy',
    'stopping'
  ]) {
    testWidgets('$mode interactions cannot authorize', (tester) async {
      final api = ConversationFixtureApi()
        ..pending = [
          interactionFixture(
              expiry: mode == 'expired' ? 1 : null,
              channels: mode == 'channel' ? ['cli'] : ['authenticated_ui'])
        ];
      final c = DesktopSession(api);
      addTearDown(c.dispose);
      await c.start();
      final entry = c.session!.pending.single;
      if (mode == 'offline') c.connected = false;
      if (mode == 'busy') c.busy = true;
      if (mode == 'stopping') c.stopping = true;
      if (mode == 'stale') c.session = Session('demo');
      await mountConversation(tester, c,
          body: SingleChildScrollView(
              child: InteractionCard(entry: entry, controller: c)));
      expect(
          tester
              .widget<FilledButton>(
                  find.widgetWithText(FilledButton, 'Approve once'))
              .onPressed,
          isNull);
      expect(
          tester
              .widget<OutlinedButton>(
                  find.widgetWithText(OutlinedButton, 'Deny'))
              .onPressed,
          isNull);
      expect(api.approvals, 0);
    });
  }

  testWidgets(
      'a question response supplies information without granting approval',
      (tester) async {
    final api = ConversationFixtureApi()
      ..pending = [interactionFixture(kind: 'question')];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    expect(find.text('Approve once'), findsNothing);
    final submit = find.widgetWithText(FilledButton, 'Answer');
    expect(tester.widget<FilledButton>(submit).onPressed, isNull);
    final answer = find.widgetWithText(TextField, 'Your answer');
    await tester.ensureVisible(answer);
    await tester.enterText(answer, '  Another folder  ');
    await tester.pump();
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(api.answers, 1);
    expect(api.response, 'Another folder');
    expect(api.approvals, 0);
  });

  testWidgets(
      'reused question IDs clear drafts when authoritative content changes',
      (tester) async {
    final api = ConversationFixtureApi()
      ..pending = [interactionFixture(kind: 'question')];
    final c = DesktopSession(api);
    addTearDown(c.dispose);
    await c.start();
    await mountConversation(tester, c);
    final answer = find.widgetWithText(TextField, 'Your answer');
    await tester.ensureVisible(answer);
    await tester.enterText(answer, 'Old request draft');
    await c.refresh();
    await tester.pump();
    expect(
        tester.widget<TextField>(answer).controller!.text, 'Old request draft');
    api.pending = [
      {...api.pending.single, 'question': 'Which new workspace?'}
    ];
    await c.refresh();
    await tester.pump();
    expect(tester.widget<TextField>(answer).controller!.text, isEmpty);
    expect(api.answers, 0);
  });

  for (final brightness in Brightness.values) {
    for (final kind in ['approval', 'question']) {
      testWidgets('$kind fits narrow large-text $brightness', (tester) async {
        final api = ConversationFixtureApi()
          ..pending = [interactionFixture(kind: kind)];
        final c = DesktopSession(api);
        addTearDown(c.dispose);
        await c.start();
        await mountConversation(tester, c,
            size: const Size(360, 640), scale: 2, brightness: brightness);
        await tester.pumpAndSettle();
        final action =
            find.text(kind == 'approval' ? 'Approve once' : 'Answer');
        await tester.ensureVisible(action);
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      });
    }
  }
}
