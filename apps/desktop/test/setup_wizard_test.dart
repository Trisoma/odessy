import 'dart:async';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/setup/controllers/setup_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/setup_wizard.dart';
import 'setup_controller_test.dart' show Backend;

void main() {
  testWidgets(
      'successful navigation never cancels coordinator during activation',
      (tester) async {
    final backend = DelayedActivationBackend();
    final visible = ValueNotifier(true);
    var cancelled = false;
    final c = SetupController(backend, onConnect: (_) async {
      visible.value = false;
    }, onCancelConnect: () async {
      cancelled = true;
    });
    c.update({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': 'existing'}
    }, '/tmp/setup.json');
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ValueListenableBuilder<bool>(
                valueListenable: visible,
                builder: (_, show, __) => show
                    ? SetupWizardPage(controller: c)
                    : const Text('Live conversation')))));
    await tester.pumpAndSettle();
    final operation = c.finish();
    await tester.pump();
    expect(find.text('Live conversation'), findsOneWidget);
    expect(cancelled, isFalse);
    expect(backend.cancelled, isFalse);
    backend.activation.complete();
    await operation;
    await tester.pumpWidget(const SizedBox());
    c.dispose();
    visible.dispose();
  });

  testWidgets('save connects automatically and details stay collapsed',
      (tester) async {
    final backend = Backend();
    var connected = false;
    final controller = SetupController(backend, onConnect: (_) async {
      connected = true;
    });
    controller.update({
      'role': 'combined',
      'model': {
        'provider': 'ollama',
        'name': 'existing',
        'endpoint': 'http://127.0.0.1:11434'
      }
    }, '/tmp/configuration.json');
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: SetupWizardPage(controller: controller))));
    for (var index = 0; index < 5; index++) {
      await tester.ensureVisible(find.text('Continue'));
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
    }
    expect(find.text('Review changes'), findsNothing);
    expect(find.text('Run diagnostics'), findsNothing);
    expect(find.textContaining('"plan_id"'), findsNothing);
    final save = find.widgetWithText(FilledButton, 'Save and connect');
    await tester.ensureVisible(save);
    await tester.tap(save);
    await tester.pumpAndSettle();
    expect(backend.requests.last.operation, 'activate');
    expect(connected, isTrue);
    expect(find.text('Connected to your configured model'), findsOneWidget);
    controller.recoveryRequired = true;
    controller.notifyListeners();
    await tester.pump();
    final field = find.byKey(const ValueKey('setup-output'));
    expect(tester.widget<TextField>(field).enabled, isTrue);
    await tester.enterText(field, '/tmp/new.json');
    await tester.pumpAndSettle();
    expect(controller.output, '/tmp/new.json');
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });

  testWidgets('advanced execution node skips model and voice setup',
      (tester) async {
    final controller = SetupController(Backend());
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: SetupWizardPage(controller: controller))));
    await tester.tap(find.text('Coordinator host + local execution'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Execution node only — advanced/headless').last);
    await tester.pumpAndSettle();
    for (var index = 0; index < 2; index++) {
      await tester.ensureVisible(find.text('Continue'));
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
    }
    expect(
        find.text(
            'Execution nodes need no LLM. Model configuration is skipped.'),
        findsOneWidget);
    await tester.ensureVisible(find.text('Continue'));
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(
        find.text(
            'Execution nodes do not need speech models or microphone access.'),
        findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}

class DelayedActivationBackend extends Backend {
  final activation = Completer<void>();
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.operation == 'activate') await activation.future;
    return super.execute(request);
  }
}
