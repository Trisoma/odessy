import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_desktop/features/workspaces/controllers/workspace_inspector_controller.dart';
import 'package:odessy_desktop/features/workspaces/presentation/workspace_inspector.dart';
import 'component_settings_test.dart' show SavedComponents;
import 'tastecode_wired_test.dart' show CaptureProjects;

class _RealHttp extends HttpOverrides {}

void main() {
  final python = Platform.environment['ODESSY_UI_TEST_PYTHON'];
  testWidgets('Python API to LocalApi to production controller and view',
      (tester) async {
    tester.view.physicalSize = const Size(1600, 1050);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await HttpOverrides.runZoned(() async {
      final process = (await tester.runAsync(() =>
          Process.start(python!, ['test/support/ui_fixture_backend.py'])))!;
      addTearDown(() async {
        process.kill();
        await process.exitCode;
      });
      final errors = <String>[];
      final stderr = process.stderr.transform(utf8.decoder).listen(errors.add);
      final lines = process.stdout
          .transform(utf8.decoder)
          .transform(const LineSplitter());
      final first = await tester
          .runAsync(() => lines.first.timeout(const Duration(seconds: 15)));
      final port = (jsonDecode(first!) as Map)['port'];
      final api =
          LocalApi(Uri.parse('http://127.0.0.1:$port'), 'offline-fixture-only');
      try {
        await tester.pumpWidget(OdessyApp(
            api: api,
            projectStore: CaptureProjects(),
            setupBackend: SavedComponents()));
        Future<void> until(bool Function() condition) async {
          for (var i = 0; i < 200; i++) {
            await tester.runAsync(
                () => Future<void>.delayed(const Duration(milliseconds: 15)));
            await tester.pump();
            if (condition()) return;
          }
          fail(
              'Service/view did not settle: ${errors.join()} ${tester.widget<ConversationPage>(find.byType(ConversationPage)).controller.error} ${tester.widget<ConversationPage>(find.byType(ConversationPage)).controller.session?.messages.map((m) => m.text).toList()}');
        }

        Future<T> io<T>(Future<T> Function() action) async {
          T? value;
          Object? error;
          bool done = false;
          action().then((v) {
            value = v;
            done = true;
          }, onError: (Object e) {
            error = e;
            done = true;
          });
          await until(() => done);
          if (error != null) throw error!;
          return value as T;
        }

        /// Bounded pumps: a pending interaction or live turn keeps scheduling
        /// frames, so `pumpAndSettle` would time out instead of asserting.
        Future<void> settleFrames() async {
          for (var i = 0; i < 8; i++) {
            await tester.pump(const Duration(milliseconds: 60));
          }
        }

        await until(() =>
            find.byType(ConversationPage).evaluate().isNotEmpty &&
            tester
                .widget<ConversationPage>(find.byType(ConversationPage))
                .controller
                .connected);
        final page =
            tester.widget<ConversationPage>(find.byType(ConversationPage));
        final session = page.controller;
        expect(api.isDemo, isFalse);
        final sid = session.session!.id;
        expect((await io(() => api.listSessions())).sessions.single.id, sid);
        Future<void> send(String text) async {
          await tester.enterText(find.byKey(const Key('composer')), text);
          await tester.pump();
          await tester.tap(find.byKey(const Key('send-or-stop')));
          await tester.pump();
        }

        await send('hello fixture');
        await until(() => session.session!.messages
            .any((m) => m.text == 'Offline response: hello fixture'));
        expect(find.text('Offline response: hello fixture'), findsOneWidget);
        await tester.tap(find.byTooltip('Files · Output · Changes').last);
        await until(() =>
            find.byType(WorkspaceInspector).evaluate().isNotEmpty &&
            tester
                    .widget<WorkspaceInspector>(find.byType(WorkspaceInspector))
                    .controller
                    .descriptor !=
                null &&
            !tester
                .widget<WorkspaceInspector>(find.byType(WorkspaceInspector))
                .controller
                .loading);
        final inspector = tester
            .widget<WorkspaceInspector>(find.byType(WorkspaceInspector))
            .controller;
        await io(() => inspector.open('readme.txt', directory: false));
        await tester.pump();
        expect(inspector.file?.text, 'Before fixture\n',
            reason: inspector.error);
        await tester.ensureVisible(find.text('Before fixture\n'));
        expect(find.text('Before fixture\n'), findsOneWidget);
        inspector.show(InspectorPanel.terminal);
        await tester.pump();
        expect(find.text('Provider fixture output; no command executed.'),
            findsOneWidget);
        await io(() async {
          try {
            await api.readWorkspaceFile(sid, '.env');
            fail('Sensitive path exposed');
          } on ApiFailure catch (e) {
            if (e.status != 403) rethrow;
          }
          try {
            await api.readWorkspaceFile(sid, '../outside');
            fail('Escape accepted');
          } on FormatException {
            // The client also rejects traversal before a request is sent.
          }
        });
        await tester.tap(find.byTooltip('Close workspace panel'));
        await tester.pump();
        await send('propose fixture change');
        await until(() => session.session!.pending.isNotEmpty);
        await tester.ensureVisible(find.text('Inspect diff'));
        // A pending approval intentionally keeps an indeterminate indicator
        // running, so pumpAndSettle cannot be used here.
        await settleFrames();
        await tester.tap(find.text('Inspect diff'));
        await until(() =>
            find.byType(WorkspaceInspector).evaluate().isNotEmpty &&
            inspector.changes.isNotEmpty);
        expect(inspector.changes.single.path, 'readme.txt');
        await tester.ensureVisible(find.text('Approve exact request'));
        await settleFrames();
        await tester.tap(find.text('Approve exact request'));
        await until(() => session.session!.messages.any(
            (m) => m.text == 'Fixture change applied after exact approval'));
        final changed =
            await io(() => api.readWorkspaceFile(sid, 'readme.txt'));
        expect(changed.text, 'After fixture\n');
        await tester.tap(find.byTooltip('Close workspace panel'));
        await tester.pump();
        await send('wait');
        await until(() => session.session!.running);
        await tester.tap(find.byKey(const Key('send-or-stop')));
        await until(() => !session.session!.running && !session.stopping);
        expect(
            session.session!.messages
                .where((m) => m.text == 'Offline response: wait'),
            isEmpty);
        await send('revoke fixture access');
        await until(() => session.session!.messages
            .any((m) => m.text == 'Fixture workspace access revoked'));
        await tester.tap(find.byTooltip('Files · Output · Changes').last);
        await until(() => inspector.error != null);
        expect(inspector.descriptor, isNull);
        expect(inspector.file, isNull);
        final voice = await io(() => api.voiceState(sid));
        expect(voice.supported, isFalse);
        expect(tester.takeException(), isNull);
      } finally {
        api.close();
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await tester.runAsync(() async {
          try {
            final client = _RealHttp().createHttpClient(null);
            final request = await client.postUrl(
                Uri.parse('http://127.0.0.1:$port/__fixture/shutdown'));
            request.headers.set('Authorization', 'Bearer offline-fixture-only');
            await (await request.close()).drain<void>();
            client.close(force: true);
            await process.exitCode.timeout(const Duration(seconds: 5));
          } catch (_) {
            process.kill();
            await process.exitCode;
          }
          await stderr.cancel();
        });
      }
    }, createHttpClient: (context) => _RealHttp().createHttpClient(context));
  }, skip: python == null);
}
