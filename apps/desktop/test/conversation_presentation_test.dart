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
