import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';

class _RealHttp extends HttpOverrides {}

void main() {
  final enabled = Platform.environment['ODESSY_REAL_PROVIDER_E2E'] == '1';
  testWidgets('desktop drives a real local model session without tool targets',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await HttpOverrides.runZoned(() async {
      final api = LocalApi(
          Uri.parse(Platform.environment['ODESSY_TEST_ENDPOINT']!),
          Platform.environment['ODESSY_TEST_TOKEN']!);
      Future<void> ready(bool Function() condition, String stage) async {
        for (var i = 0; i < 600; i++) {
          await tester.runAsync(
              () => Future<void>.delayed(const Duration(milliseconds: 50)));
          await tester.pump(const Duration(milliseconds: 50));
          if (condition()) return;
        }
        final page = find.byType(ConversationPage).evaluate().isEmpty
            ? null
            : tester.widget<ConversationPage>(find.byType(ConversationPage));
        final c = page?.controller;
        fail('Local provider UI did not settle: $stage; '
            'connected=${c?.connected}, busy=${c?.busy}, stopping=${c?.stopping}, '
            'running=${c?.session?.running}, status=${c?.status}, error=${c?.error}, '
            'events=${c?.session?.events.map((e) => e.type).toList()}');
      }

      try {
        await tester.pumpWidget(OdessyApp(api: api));
        await ready(
            () =>
                find.byType(ConversationPage).evaluate().isNotEmpty &&
                tester
                    .widget<ConversationPage>(find.byType(ConversationPage))
                    .controller
                    .connected,
            'initial connection');
        final controller = tester
            .widget<ConversationPage>(find.byType(ConversationPage))
            .controller;
        expect(find.text('DEMO'), findsNothing);
        expect(controller.session!.selectedProfile, 'local-a');

        await tester.tap(find.byType(DropdownButtonFormField<String>).first);
        await tester.pump(const Duration(milliseconds: 500));
        await tester.tap(find.text('local-b').last);
        await tester.pump();
        await ready(() => controller.session?.selectedProfile == 'local-b',
            'next-turn model selection');

        const greeting = 'Hello! Reply with one short English greeting.';
        await tester.enterText(find.byKey(const Key('composer')), greeting);
        await tester.pump();
        await tester.tap(find.byTooltip('Send message'));
        await tester.pump();
        await ready(
            () =>
                controller.session?.messages.any(
                    (m) => m.role == 'assistant' && m.text.trim().isNotEmpty) ==
                true,
            'real model response');
        expect(controller.session!.selectedProfile, 'local-b');
        final before = controller.session!.messages
            .where((m) => m.role == 'assistant')
            .length;
        expect(find.text(greeting), findsOneWidget);

        await tester.enterText(find.byKey(const Key('composer')),
            'Rewrite this paragraph in detail: ${'hello world. ' * 80}');
        await tester.pump();
        await tester.tap(find.byTooltip('Send message'));
        await tester.pump();
        await ready(
            () => find.byTooltip('Stop current turn').evaluate().isNotEmpty,
            'real model running');
        await tester.tap(find.byTooltip('Stop current turn'));
        await tester.pump();
        await ready(
            () =>
                controller.status.startsWith('Stopped') &&
                controller.session?.running == false &&
                !controller.stopping &&
                !controller.busy,
            'stop confirmation');
        expect(
            controller.session!.messages
                .where((m) => m.role == 'assistant')
                .length,
            before);

        await tester.tap(find.byType(DropdownButtonFormField<String>).first);
        await tester.pump(const Duration(milliseconds: 500));
        await tester.tap(find.text('missing-model').last);
        await tester.pump();
        await ready(
            () =>
                controller.session?.selectedProfile == 'missing-model' &&
                !controller.busy &&
                !controller.stopping,
            'unavailable model selection');
        await tester.enterText(
            find.byKey(const Key('composer')), 'Hello again.');
        await tester.pump();
        await ready(
            () =>
                tester
                    .widget<IconButton>(find.byKey(const Key('send-or-stop')))
                    .onPressed !=
                null,
            'send enabled after model selection');
        await tester.tap(find.byTooltip('Send message'));
        await tester.pump();
        await ready(() => controller.error.contains('The turn failed'),
            'unavailable model failure');
        expect(
            controller.session!.messages
                .where((m) => m.role == 'assistant')
                .length,
            before);
        expect(find.text('DEMO'), findsNothing);
        api.close();
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 20)));
        await tester.pump();
      } finally {
        api.close();
      }
    }, createHttpClient: (context) => _RealHttp().createHttpClient(context));
  }, skip: !enabled);

  testWidgets('real model approval denial never dispatches the fixture tool',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await HttpOverrides.runZoned(() async {
      final api = LocalApi(
          Uri.parse(Platform.environment['ODESSY_TEST_ENDPOINT']!),
          Platform.environment['ODESSY_TEST_TOKEN']!);
      Future<void> ready(bool Function() condition, String stage) async {
        for (var i = 0; i < 600; i++) {
          await tester.runAsync(
              () => Future<void>.delayed(const Duration(milliseconds: 50)));
          await tester.pump(const Duration(milliseconds: 50));
          if (condition()) return;
        }
        fail('Local provider approval did not settle: $stage');
      }

      try {
        await tester.pumpWidget(OdessyApp(api: api));
        await ready(
            () =>
                find.byType(ConversationPage).evaluate().isNotEmpty &&
                tester
                    .widget<ConversationPage>(find.byType(ConversationPage))
                    .controller
                    .connected,
            'initial connection');
        final controller = tester
            .widget<ConversationPage>(find.byType(ConversationPage))
            .controller;
        await tester.tap(find.byType(DropdownButtonFormField<String>).first);
        await tester.pump(const Duration(milliseconds: 500));
        await tester.tap(find.text('tool-local').last);
        await tester.pump();
        await ready(
            () =>
                controller.session?.selectedProfile == 'tool-local' &&
                !controller.busy,
            'tool model selected');
        await tester.enterText(find.byKey(const Key('composer')),
            'Use terminal.run to run mkdir fixture-folder. Do not assume it succeeded.');
        await tester.pump();
        await ready(
            () =>
                tester
                    .widget<IconButton>(find.byKey(const Key('send-or-stop')))
                    .onPressed !=
                null,
            'send enabled');
        await tester.tap(find.byTooltip('Send message'));
        await tester.pump();
        await ready(
            () =>
                controller.session?.pending.isNotEmpty == true &&
                find.text('Approval required').evaluate().isNotEmpty,
            'real model approval request');
        final request = controller.session!.pending.single;
        expect(request['operation'], 'terminal.run');
        expect(
            (request['arguments'] as Map)['command'], 'mkdir fixture-folder');
        await ready(
            () =>
                find
                    .widgetWithText(OutlinedButton, 'Deny')
                    .evaluate()
                    .isNotEmpty &&
                tester
                        .widget<OutlinedButton>(
                            find.widgetWithText(OutlinedButton, 'Deny'))
                        .onPressed !=
                    null,
            'deny enabled');
        final denyButton = find.widgetWithText(OutlinedButton, 'Deny');
        await tester.ensureVisible(denyButton);
        await tester.pump();
        expect(denyButton.hitTestable(), findsOneWidget);
        await tester.tap(denyButton);
        await tester.pump();
        await ready(
            () =>
                controller.session?.pending.isEmpty == true &&
                controller.session?.running == false &&
                !controller.busy,
            'denial resolved');
        expect(find.text('DEMO'), findsNothing);
        api.close();
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 20)));
        await tester.pump();
      } finally {
        api.close();
      }
    }, createHttpClient: (context) => _RealHttp().createHttpClient(context));
  }, skip: !enabled);
}
