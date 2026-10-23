import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/maintenance_page.dart';
import 'setup_controller_test.dart' show Backend;

void main() {
  testWidgets('maintenance shows review gate and real action details',
      (tester) async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: MaintenancePage(controller: c))));
    await tester.enterText(
        find.byKey(const ValueKey('native-root')), '/tmp/runtime');
    await tester.enterText(
        find.byKey(const ValueKey('native-bundle')), '/tmp/bundle');
    final confirm =
        find.widgetWithText(FilledButton, 'Confirm reviewed operation');
    expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
    await tester.ensureVisible(find.text('Review operation'));
    await tester.tap(find.text('Review operation'));
    await tester.pumpAndSettle();
    expect(backend.requests.last.parameters['action'], 'repair-plan');
    expect(tester.widget<FilledButton>(confirm).onPressed, isNotNull);
  });
  testWidgets('pairing explains independent verification and local grants',
      (tester) async {
    final c = MaintenanceController(Backend());
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: MaintenancePage(controller: c, pairing: true))));
    expect(find.textContaining('Enrollment is not permission'), findsOneWidget);
    expect(find.byKey(const ValueKey('native-fingerprint')), findsOneWidget);
  });
}
