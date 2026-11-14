import 'dart:convert';
import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_desktop/features/history/presentation/basics_page.dart';
import 'component_settings_test.dart' show SavedComponents;

void main() {
  final python = Platform.environment['ODESSY_PREFLIGHT_PYTHON'];
  testWidgets('production shell sends through real Python durable service',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final root = Directory.current.parent.parent;
    Process? process;
    LocalApi? api;
    final oldOverrides = HttpOverrides.current;
    HttpOverrides.global = null;
    try {
      final url = await tester.runAsync(() async {
        process = await Process.start(
            python!,
            [
              '-c',
              "import site,runpy; site.addsitedir(r'${Platform.environment['ODESSY_PREFLIGHT_SITE']}'); runpy.run_path('tool/agent_basics_preflight_fixture.py',run_name='__main__')"
            ],
            workingDirectory: root.path,
            environment: {
              'PYTHONPATH': '${root.path}${Platform.pathSeparator}src'
            });
        final error = StringBuffer();
        process!.stderr.transform(utf8.decoder).listen(error.write);
        final line = await process!.stdout
            .transform(utf8.decoder)
            .transform(const LineSplitter())
            .firstWhere((line) => line.startsWith('PREFLIGHT_URL='))
            .timeout(const Duration(seconds: 20),
                onTimeout: () => throw StateError('Fixture startup: $error'));
        return line.substring('PREFLIGHT_URL='.length);
      });
      api = LocalApi(Uri.parse(url!), 'preflight-fixture-only');
      await tester
          .pumpWidget(OdessyApp(api: api, setupBackend: SavedComponents()));
      for (var i = 0; i < 60; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 30)));
        await tester.pump(const Duration(milliseconds: 50));
        final page = find.byType(ConversationPage);
        if (page.evaluate().isNotEmpty &&
            tester.widget<ConversationPage>(page).controller.session != null) {
          break;
        }
      }
      await tester.enterText(
          find.byKey(const Key('composer')), 'boat-network-check');
      await tester.pump();
      await tester.tap(find.byTooltip('Send message'));
      for (var i = 0;
          i < 80 &&
              find
                  .textContaining('Connected preflight reply:')
                  .evaluate()
                  .isEmpty;
          i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 30)));
        await tester.pump(const Duration(milliseconds: 50));
      }
      expect(
          find.textContaining('Connected preflight reply: boat-network-check'),
          findsOneWidget);
      final live = tester
          .widget<ConversationPage>(find.byType(ConversationPage))
          .controller;
      final cid = live.session!.conversationId;
      expect(cid, isNotNull);
      await tester.runAsync(() async {
        // Keep this standalone wire audit in the real async zone. The shell's
        // own HttpClient is pumped separately by the widget binding below.
        final audit = LocalApi(api!.base, 'preflight-fixture-only');
        try {
          final client = audit.agentBasics;
          final cap = await client.capabilities();
          expect(cap.supports('conversations'), isTrue);
          expect(cap.supports('terminal'), isFalse);
          final saved = await client.conversation(cid!);
          await client.update(saved, title: 'Network-reviewed conversation');
          final page = await client.messages(cid);
          expect(
              page.events
                  .where((e) => e.kind == 'assistant')
                  .single
                  .payload['text'],
              contains('boat-network-check'));
          final match = await client.search('boat-network-check');
          expect(match.matches.any((m) => m.conversationId == cid), isTrue);
          final project = await client.createProject('HTTP lifecycle test');
          final empty = await client.createConversation(project.id);
          final pinned = await client.update(empty, pinned: true);
          expect(pinned.pinned, isTrue);
          final exported = await client.export(pinned.id);
          expect((exported['conversation'] as Map)['project_id'], project.id);
          await client.delete(pinned);
          await expectLater(
              client.conversation(pinned.id),
              throwsA(
                  isA<ApiFailure>().having((e) => e.status, 'status', 404)));
          final mode = await client.mode(live.session!.id);
          expect(mode.serviceInstance, cap.serviceInstance);
          expect(mode.mode, AccessMode.ask);
        } finally {
          audit.close();
        }
      });
      for (var i = 0; i < 80; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 30)));
        await tester.pump(const Duration(milliseconds: 50));
        if (tester
                .widget<TextField>(find.byKey(const Key('composer')))
                .enabled ==
            true) {
          break;
        }
      }
      await tester.enterText(
          find.byKey(const Key('composer')), 'retain this unsent draft');
      expect(
          tester
              .widget<TextField>(find.byKey(const Key('composer')))
              .controller!
              .text,
          'retain this unsent draft');
      await tester.ensureVisible(find.text('Saved history & tools'));
      await tester.tap(find.text('Saved history & tools'));
      for (var i = 0; i < 80; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 30)));
        await tester.pump(const Duration(milliseconds: 50));
        if (find.byType(BasicsPage).evaluate().isNotEmpty &&
            !tester
                .widget<BasicsPage>(find.byType(BasicsPage))
                .controller
                .busy) {
          break;
        }
      }
      final history = tester.widget<BasicsPage>(find.byType(BasicsPage));
      expect(history.controller.error, isNull);
      unawaited(history.controller.open(cid!));
      for (var i = 0; i < 80 && history.controller.busy; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 30)));
        await tester.pump(const Duration(milliseconds: 50));
      }
      expect(
          history.controller.selected!.title, 'Network-reviewed conversation');
      expect(
          history.controller.events.any((e) => e.kind == 'assistant'), isTrue);
      // The same production page owns the same external draft; browsing has not
      // switched live session, model, grants or sent another turn.
      expect(live.session!.id, history.controller.session.session!.id);
      await tester.tap(find.byTooltip('Chat').first);
      await tester.pump();
      expect(
          tester
              .widget<TextField>(find.byKey(const Key('composer')))
              .controller!
              .text,
          'retain this unsent draft');
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      api?.close();
      await tester.runAsync(() async {
        if (process != null) {
          process!.stdin.writeln('stop');
          await process!.stdin.close();
          await process!.exitCode.timeout(const Duration(seconds: 10));
        }
      });
      HttpOverrides.global = oldOverrides;
    }
  }, skip: python == null);
}
