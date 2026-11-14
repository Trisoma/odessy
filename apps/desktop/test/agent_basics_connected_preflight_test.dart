import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
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
