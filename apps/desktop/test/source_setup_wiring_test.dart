import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/setup/disconnected_api.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';
import 'setup_controller_test.dart' show RestoringBackend;

class UnavailableCoordinator implements InstalledCoordinatorHandle {
  @override
  Future<LocalApi> start() async =>
      throw const CoordinatorStartFailure('Test coordinator unavailable');
  @override
  Future<void> stop() async {}
}

void main() {
  testWidgets('a failed owned start can be retried after configuring a key', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var attempts = 0;
    await tester.pumpWidget(OdessyApp(api: DisconnectedApi(), restoreConfiguration: true,
      setupBackend: RestoringBackend(), coordinatorFactory: (_) {
        attempts++;
        return UnavailableCoordinator();
      }));
    await tester.pumpAndSettle();
    expect(attempts, 1);
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Models and connections'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Connect saved configuration').last);
    await tester.tap(find.text('Connect saved configuration').last);
    await tester.pumpAndSettle();
    expect(attempts, 2);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'restored source settings show configured even when backend is offline',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(
        api: DisconnectedApi(),
        restoreConfiguration: true,
        setupBackend: RestoringBackend(),
        coordinatorFactory: (_) => UnavailableCoordinator()));
    await tester.pumpAndSettle();
    expect(find.text('DEMO'), findsNothing);
    expect(find.textContaining('This is a preview response'), findsNothing);
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Models and connections'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('component-name')), findsOneWidget);
    expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('component-name')))
            .controller!
            .text,
        'saved-q8');
    expect(find.text('Continue'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
