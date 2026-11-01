import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';
import 'package:odessy_desktop/app/setup/local_desktop_application.dart';

class FailingCoordinator implements InstalledCoordinatorHandle {
  int starts = 0, stops = 0;
  @override
  Future<LocalApi> start() async {
    starts++;
    throw const CoordinatorStartFailure('Local service unavailable');
  }

  @override
  Future<void> stop() async {
    stops++;
  }
}

class SessionApi extends DemoApi {
  int deletes = 0, creates = 0;
  @override
  Future<Session> createSession() {
    creates++;
    return super.createSession();
  }

  @override
  Future<void> deleteSession(String sid) async {
    deletes++;
  }
}

void main() {
  testWidgets('default app starts disconnected and does not silently use demo',
      (tester) async {
    await tester.pumpWidget(const OdessyApp());
    await tester.pumpAndSettle();
    expect(find.text('DEMO'), findsNothing);
    expect(find.text('Connect to your local service to continue.'),
        findsOneWidget);
    await tester.tap(find.byTooltip('Connect local service'));
    await tester.pumpAndSettle();
    expect(find.text('Use demo'), findsNothing);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('local desktop connection does not offer demo mode',
      (tester) async {
    final api = SessionApi();
    await tester.pumpWidget(OdessyApp(api: api, localDesktop: true));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Connect local service'));
    await tester.pumpAndSettle();
    expect(find.text('Use demo'), findsNothing);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox.shrink());
  });
  testWidgets('service failures are visible and only retried explicitly',
      (tester) async {
    final coordinator = FailingCoordinator();
    await tester.pumpWidget(LocalDesktopApplication(coordinator: coordinator));
    await tester.pumpAndSettle();
    expect(find.text('Local service could not start'), findsOneWidget);
    expect(find.text('Local service unavailable'), findsOneWidget);
    expect(coordinator.starts, 1);
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(coordinator.starts, 2);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('new session requires confirmation and does not close on cancel',
      (tester) async {
    final api = SessionApi();
    await tester.pumpWidget(OdessyApp(api: api, localDesktop: true));
    await tester.pumpAndSettle();
    expect(api.creates, 1);
    await tester.tap(find.byTooltip('Start new session'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    expect(api.deletes, 0);
    await tester.tap(find.byTooltip('Start new session'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Start new session'));
    await tester.pumpAndSettle();
    await tester.runAsync(() async {
      for (var i = 0; i < 30 && api.creates < 2; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
    });
    await tester.pump();
    expect(api.deletes, 1);
    expect(api.creates, 2);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
