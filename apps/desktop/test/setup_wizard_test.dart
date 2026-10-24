import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/setup/controllers/setup_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/setup_wizard.dart';
import 'setup_controller_test.dart' show Backend;

void main() {
  testWidgets(
      'wizard requires review before save and reports configuration only',
      (tester) async {
    final backend = Backend();
    final controller = SetupController(backend);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: SetupWizardPage(controller: controller))));
    expect(find.text('Set up this computer'), findsOneWidget);
    for (var index = 0; index < 5; index++) {
      await tester.ensureVisible(find.text('Continue'));
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
    }
    await tester.enterText(
        find.byKey(const ValueKey('setup-output')), '/tmp/configuration.json');
    final save =
        find.widgetWithText(FilledButton, 'Confirm & save configuration');
    expect(tester.widget<FilledButton>(save).onPressed, isNull);
    await tester.ensureVisible(find.text('Review changes'));
    await tester.tap(find.text('Review changes'));
    await tester.pumpAndSettle();
    expect(tester.widget<FilledButton>(save).onPressed, isNotNull);
    await tester.tap(save);
    await tester.pumpAndSettle();
    expect(backend.requests.last.operation, 'save');
    expect(
        find.text(
            'Configuration saved — components are not installed or started'),
        findsOneWidget);
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
