import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/setup/controllers/installed_manage_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/manage_page.dart';

import 'installed_manage_controller_test.dart' show Backend;

void main() {
  testWidgets('manage shows recorded state and requires an exact review',
      (tester) async {
    final backend = Backend();
    final c =
        InstalledManageController(backend, spec: {'root': 'C:/Odessy/App'});
    await c.load();
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ManagePage(controller: c))));
    expect(find.text('Manage Odessy'), findsOneWidget);
    expect(find.textContaining('combined'), findsOneWidget);
    expect(find.text('Confirm once'), findsNothing);

    await tester.ensureVisible(find.text('Repair or update files'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Repair or update files'));
    await tester.pumpAndSettle();
    expect(backend.requests.last.parameters['action'], 'package-repair-review');
    expect(find.text('Review before confirming'), findsOneWidget);
    final confirm = find.widgetWithText(FilledButton, 'Confirm once');
    expect(tester.widget<FilledButton>(confirm).onPressed, isNotNull);

    await tester.ensureVisible(confirm);
    await tester.pumpAndSettle();
    await tester.tap(confirm);
    await tester.pumpAndSettle();
    expect(
        backend.requests
            .where(
                (request) => request.parameters['action'] == 'package-repair')
            .single
            .parameters['confirm'],
        'repair-1');
  });

  testWidgets('uninstall is delegated when the app is inside the installation',
      (tester) async {
    final backend = Backend();
    final c =
        InstalledManageController(backend, spec: {'root': 'C:/Odessy/App'});
    await c.load();
    var opened = false;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ManagePage(
                controller: c,
                external: false,
                onOpenMaintenance: () => opened = true))));
    expect(find.text('Review uninstall'), findsNothing);
    await tester.ensureVisible(find.text('Open maintenance to uninstall'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open maintenance to uninstall'));
    expect(opened, isTrue);
  });

  testWidgets('an external maintenance window offers reviewed uninstall',
      (tester) async {
    final backend = Backend();
    final c =
        InstalledManageController(backend, spec: {'root': 'C:/Odessy/App'});
    await c.load();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ManagePage(controller: c, external: true))));
    expect(find.textContaining('Only files recorded'), findsOneWidget);
    await tester.ensureVisible(find.text('Review uninstall'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Review uninstall'));
    await tester.pumpAndSettle();
    expect(
        backend.requests.last.parameters['action'], 'package-uninstall-review');
    expect(
        find.textContaining('Existing models are preserved'), findsOneWidget);
  });
}
