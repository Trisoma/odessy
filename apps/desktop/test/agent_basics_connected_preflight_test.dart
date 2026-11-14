import 'dart:convert';
import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_desktop/features/history/presentation/basics_page.dart';
import 'package:odessy_desktop/app/preferences/desktop_preferences.dart';
import 'package:odessy_desktop/app/preferences/shell_layout_store.dart';
import 'component_settings_test.dart' show SavedComponents;

class _Preferences implements DesktopPreferencesStore {
  PreferencesSnapshot value =
      const PreferencesSnapshot(DesktopPreferences(), 0);
  @override
  Future<PreferencesSnapshot> load() async => value;
  @override
  Future<PreferencesSnapshot> save(DesktopPreferences next,
          {required int revision}) async =>
      value = PreferencesSnapshot(next, revision + 1);
}

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
    final captureKey = GlobalKey();
    final captureDir = Platform.environment['ODESSY_07_CAPTURE_DIR'];
    Future<void> capture(String name) async {
      if (captureDir == null) return;
      await tester.pump(const Duration(milliseconds: 200));
      expect(tester.takeException(), isNull, reason: name);
      await tester.runAsync(() async {
        final boundary = captureKey.currentContext!.findRenderObject()!
            as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 1);
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory(captureDir).create(recursive: true);
        await File('$captureDir/$name.png')
            .writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
    }

    Future<void> settleNetwork() async {
      for (var i = 0; i < 15; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 30)));
        await tester.pump(const Duration(milliseconds: 50));
      }
    }

    try {
      for (final (family, asset) in [
        ('OdessySans', 'assets/fonts/roboto-regular.ttf'),
        ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf')
      ]) {
        await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
      }
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
      await tester.pumpWidget(RepaintBoundary(
          key: captureKey,
          child: OdessyApp(
              api: api,
              setupBackend: SavedComponents(),
              preferencesStore: _Preferences(),
              shellLayoutStore: MemoryShellLayoutStore())));
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
          expect(cap.supports('memory'), isTrue);
          expect(cap.supports('retrieval'), isTrue);
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
      expect(find.text('Tools & installation'), findsNothing);
      expect(find.text('Runtime maintenance'), findsNothing);
      await capture('01-chat-dark-wide');
      await tester.tap(find.byTooltip('Open workspace inspector'));
      await settleNetwork();
      await capture('02-inspector-dark-wide');
      await tester.tap(find.byTooltip('Close workspace inspector'));
      await tester.pump();
      if (captureDir != null) {
        tester.view.physicalSize = const Size(1200, 850);
        await tester.pump();
        await tester.tap(find.byTooltip('Settings').first);
        await settleNetwork();
        for (final (section, name) in [
          ('General', 'settings-general'),
          ('Models and connections', 'settings-models'),
          ('Speech to text (STT)', 'settings-speech-to-text'),
          ('Text to speech (TTS)', 'settings-text-to-speech'),
          ('Voice', 'settings-voice'),
          ('Devices and tool access', 'settings-devices-and-tools'),
          ('Advanced', 'settings-advanced'),
          ('Installation', 'settings-installation'),
        ]) {
          await tester.tap(find.byKey(ValueKey('settings-section-$section')));
          await settleNetwork();
          await capture(name);
        }
        await tester
            .tap(find.byKey(const ValueKey('settings-section-Installation')));
        await tester.pump();
        expect(find.text('Runtime maintenance'), findsOneWidget);
        await capture('10-settings-installation');
        await tester.tap(find
            .byKey(const ValueKey('settings-section-Devices and tool access')));
        await tester.pump();
        await tester.ensureVisible(find.text('Open local pairing tools'));
        await capture('11-settings-pairing-tools');
        for (final (destination, name) in [
          ('Activity & usage', 'activity-and-usage'),
          ('Devices', 'devices'),
          ('Workspaces', 'workspaces'),
          ('Jobs', 'jobs'),
          ('Handoffs', 'handoffs'),
        ]) {
          await tester.tap(find.byTooltip(destination).first);
          await settleNetwork();
          await capture(name);
        }
        await tester.tap(find.byTooltip('Chat').first);
        await tester.pump();
      }
      expect(
          tester
              .widget<TextField>(find.byKey(const Key('composer')))
              .controller!
              .text,
          'retain this unsent draft');
      await tester.tap(find.byTooltip('Search chats').first);
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
      await capture('03-history-dark-wide');
      if (captureDir != null) {
        final selectedProject = history.controller.projects.firstWhere(
            (project) => project.id == history.controller.selected!.projectId);
        final projectTile = find.descendant(
            of: find
                .byKey(PageStorageKey('saved-project-${selectedProject.id}')),
            matching: find.text(selectedProject.label));
        await tester.tap(projectTile);
        await tester.pump(const Duration(milliseconds: 300));
        await capture('13-sidebar-expanded-project');
        await tester.tap(projectTile);
        await tester.pump(const Duration(milliseconds: 300));
        await tester.runAsync(() =>
            history.controller.saveMemory('Prefer concise boat explanations.'));
        await tester.runAsync(() => history.controller.index('boat.md'));
        for (final name in [
          'Context',
          'Memory',
          'Documents',
          'Terminal',
          'Checkpoints'
        ]) {
          if (name == 'Context') {
            await tester.runAsync(history.controller.loadContext);
          }
          if (name == 'Memory') {
            await tester.runAsync(history.controller.loadMemory);
          }
          if (name == 'Documents') {
            await tester.runAsync(history.controller.loadDocuments);
          }
          if (name == 'Checkpoints') {
            await tester.runAsync(history.controller.loadCheckpoints);
          }
          await tester.ensureVisible(find.text(name).first);
          await tester.tap(find.text(name).first);
          await tester.pump();
          if (name == 'Terminal' &&
              history.controller.capabilities?.supports('terminal') == true) {
            await tester.runAsync(() async {
              unawaited(history.controller.createTerminal());
            });
            await settleNetwork();
            await capture('04-terminal-pending-approval');
            expect(live.session!.pending, isNotEmpty);
            await tester.tap(find.text('Approve once').first);
            await settleNetwork();
            expect(live.error, isEmpty);
            expect(live.session!.pending, isEmpty);
            for (var i = 0; i < 100 && history.controller.busy; i++) {
              await tester.runAsync(
                  () => Future<void>.delayed(const Duration(milliseconds: 50)));
              await tester.pump(const Duration(milliseconds: 50));
            }
            expect(history.controller.terminal, isNotNull,
                reason:
                    '${history.controller.error}; busy=${history.controller.busy}; session=${live.status}');
            // ConPTY allocation precedes the shell's first prompt on Windows.
            for (var i = 0;
                i < 200 && !history.controller.terminalText.contains('>');
                i++) {
              await tester.runAsync(
                  () => Future<void>.delayed(const Duration(milliseconds: 50)));
              await tester.runAsync(history.controller.readTerminal);
              await tester.pump();
            }
            unawaited(history.controller.input('echo ODESSY_07_SCREENSHOT\r'));
            for (var i = 0; i < 300 && history.controller.busy; i++) {
              await tester.runAsync(
                  () => Future<void>.delayed(const Duration(milliseconds: 50)));
              await tester.pump(const Duration(milliseconds: 50));
            }
            expect(history.controller.error, isNull,
                reason: 'Terminal input must be acknowledged');
            await settleNetwork();
            for (var i = 0;
                i < 100 &&
                    !history.controller.terminalText
                        .contains('ODESSY_07_SCREENSHOT');
                i++) {
              await tester.runAsync(
                  () => Future<void>.delayed(const Duration(milliseconds: 50)));
              await tester.runAsync(history.controller.readTerminal);
              await tester.pump();
            }
            expect(history.controller.terminalText,
                contains('ODESSY_07_SCREENSHOT'));
          }
          if (name == 'Checkpoints') {
            await tester.runAsync(() async {
              unawaited(
                  history.controller.createCheckpoint('Before UI preview'));
            });
            await settleNetwork();
            expect(live.session!.pending, isNotEmpty);
            await tester.tap(find.text('Approve once').first);
            for (var i = 0; i < 200 && history.controller.busy; i++) {
              await tester.runAsync(
                  () => Future<void>.delayed(const Duration(milliseconds: 50)));
              await tester.pump(const Duration(milliseconds: 50));
            }
            expect(history.controller.error, isNull);
            expect(history.controller.checkpoints, isNotEmpty);
            unawaited(history.controller
                .previewCheckpoint(history.controller.checkpoints.first));
            for (var i = 0; i < 300 && history.controller.busy; i++) {
              await tester.runAsync(
                  () => Future<void>.delayed(const Duration(milliseconds: 50)));
              await tester.pump(const Duration(milliseconds: 50));
            }
            expect(history.controller.error, isNull);
            expect(history.controller.preview, isNotNull);
          }
          await capture('04-${name.toLowerCase()}-dark-wide');
        }
        await tester.tap(find.byTooltip('Toggle light / dark theme'));
        await settleNetwork();
        await capture('05-checkpoints-light-wide');
        tester.view.physicalSize = const Size(420, 850);
        await tester.pump();
        await capture('06-tools-light-narrow');
        tester.view.physicalSize = const Size(1200, 850);
        await tester.pump();
      }
      await tester.tap(find.byTooltip('Chat').first);
      await tester.pump();
      await capture('07-chat-light-wide');
      if (captureDir != null) {
        tester.view.physicalSize = const Size(420, 850);
        await tester.pump();
        await capture('08-chat-light-narrow');
        tester.view.physicalSize = const Size(1200, 850);
        await tester.pump();
        await tester.tap(find.byTooltip('Toggle light / dark theme'));
        await settleNetwork();
        tester.view.physicalSize = const Size(420, 850);
        await tester.pump();
        await capture('09-chat-dark-narrow');
      }
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
