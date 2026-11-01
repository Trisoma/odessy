import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'conversation_presentation_test.dart';

void main() {
  testWidgets('render conversation and interaction screenshots',
      (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    await (FontLoader('OdessyMono')
          ..addFont(rootBundle.load('assets/fonts/roboto-mono.ttf')))
        .load();
    tester.view.physicalSize = const Size(1440, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final key = GlobalKey();
    final api = ConversationFixtureApi()
      ..messages = [
        const ChatMessage('user', 'Check the repository status.'),
        const ChatMessage('assistant',
            'I will inspect the changes after you review the requested action.\n'
            '\x60\x60\x60sh\ngit status --short\n\x60\x60\x60'),
      ];
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final frame = await boundary.toImage(pixelRatio: 1);
      final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/screenshots/conversation').create(recursive: true);
      await File('build/screenshots/conversation/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      frame.dispose();
    }

    await tester
        .pumpWidget(RepaintBoundary(key: key, child: OdessyApp(api: api)));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('conversation-dark'));
    api.pending = [interactionFixture()];
    final page = tester.widget<ConversationPage>(find.byType(ConversationPage));
    await page.controller.refresh();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Approve once'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('approval-dark'));
    api.pending = [];
    api.messages = [
      const ChatMessage('user', 'Show an example of a tool activity report.'),
      const ChatMessage('assistant',
          'This is an illustrative report. No command was executed.'),
    ];
    api.activity = [
      SessionEvent(1, 'result', {
        'tool_request': {
          'name': 'git.diff',
          'arguments': {'path': 'README.md'}
        },
        'tool_result': {'demo': true, 'output': 'Example diff summary'},
      })
    ];
    await page.controller.refresh();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Activity & provenance'));
    await tester.tap(find.text('Activity & provenance'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Turn result'));
    await tester.tap(find.text('Turn result'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.textContaining('Example diff summary'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('activity-dark'));
    api.activity = [
      SessionEvent(2, 'cost', {
        'total': {'calls': 2, 'prompt': 1200, 'cached': 300, 'completion': 200},
        'total_cost': null,
      })
    ];
    await page.controller.refresh();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('API usage and estimated cost'));
    await tester.tap(find.text('API usage and estimated cost'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.textContaining('process-wide'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('usage-dark'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('usage-light'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    api.pending = [interactionFixture()];
    await page.controller.refresh();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Approve once'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('approval-light'));
    api.pending = [interactionFixture(kind: 'question')];
    await page.controller.refresh();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Answer'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('question-light'));

    final narrowApi = ConversationFixtureApi()
      ..pending = [interactionFixture()];
    final controller = DesktopSession(narrowApi);
    addTearDown(controller.dispose);
    await controller.start();
    tester.view.physicalSize = const Size(390, 1000);
    await tester.pumpWidget(RepaintBoundary(
        key: key,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: odessyTheme(Brightness.dark),
          home: Scaffold(body: ConversationPage(controller: controller)),
        )));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Approve once'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('approval-narrow'));
    expect(tester.takeException(), isNull);
  });
}
