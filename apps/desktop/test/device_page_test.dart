import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/devices.dart';

class InspectApi extends DemoApi {
  InspectApi({this.demo = true, this.running = false});
  final bool demo, running;
  bool failConnection = false;
  Completer<List<String>>? profileGate;
  int creates = 0, reads = 0, submissions = 0, selections = 0, saves = 0;

  @override
  bool get isDemo => demo;
  @override
  String get identity => 'http://127.0.0.1:4321';
  @override
  Future<List<String>> profiles() async {
    if (failConnection) throw const ApiFailure(503);
    return profileGate?.future ?? super.profiles();
  }

  @override
  Future<Session> createSession() async {
    creates++;
    return snapshot('demo');
  }

  @override
  Future<Session> snapshot(String sid) async {
    reads++;
    return Session('demo', running: running, selectedProfile: 'local');
  }

  @override
  Future<void> send(String sid, String text) async => submissions++;
  @override
  Future<void> select(String sid, String profile) async => selections++;
  @override
  Future<Json> saveSettings(Json value) async {
    saves++;
    return value;
  }
}

Future<void> mountDevice(
  WidgetTester tester,
  DesktopSession controller, {
  String device = 'Jetson',
  Size size = const Size(1200, 1000),
  double scale = 1,
  Brightness brightness = Brightness.dark,
  VoidCallback? onManagePairing,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      theme: odessyTheme(brightness),
      home: MediaQuery(
        data: MediaQueryData(size: size, textScaler: TextScaler.linear(scale)),
        child: Scaffold(
          body: DevicePage(
              device: device,
              controller: controller,
              onManagePairing: onManagePairing),
        ),
      ),
    ),
  );
  await tester.pump();
}

void main() {
  testWidgets('app opens local pairing from device settings', (tester) async {
    tester.view.physicalSize = const Size(1200, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(api: DemoApi(delay: Duration.zero)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Settings').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Manage pairing'));
    await tester.tap(find.text('Manage pairing'));
    await tester.pumpAndSettle();
    expect(find.text('Verified device pairing'), findsOneWidget);
    expect(find.text('Independently verified SHA-256 fingerprint'),
        findsOneWidget);
    expect(find.text('DEMO'), findsOneWidget);
  });

  testWidgets('pairing entry only opens local tools without granting access',
      (tester) async {
    final api = InspectApi(demo: false);
    final controller = DesktopSession(api);
    addTearDown(controller.dispose);
    await controller.start();
    var opened = 0;
    await mountDevice(tester, controller, onManagePairing: () => opened++);
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Manage pairing'));
    await tester.tap(find.text('Manage pairing'));
    expect(opened, 1);
    expect(api.submissions + api.selections + api.saves, 0);
    expect(controller.session!.selectedProfile, 'local');
  });

  testWidgets('inspection separates service connectivity from device state', (
    tester,
  ) async {
    final api = InspectApi(demo: false);
    final controller = DesktopSession(api);
    addTearDown(controller.dispose);
    await controller.start();
    await mountDevice(tester, controller);
    expect(find.text('Connected'), findsOneWidget);
    expect(find.text('Unknown'), findsOneWidget);
    expect(find.text('Not verified'), findsOneWidget);
    expect(find.text('Read-only device view'), findsOneWidget);
    expect(find.textContaining('Demo device'), findsNothing);
    expect(find.text('Unavailable'), findsNWidgets(3));

    final readsBefore = api.reads;
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Manage pairing'),
          )
          .onPressed,
      isNull,
    );
    expect(
      tester
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Configure audio'),
          )
          .onPressed,
      isNull,
    );
    expect(api.reads, readsBefore);
    expect(api.creates, 1);
    expect(api.submissions + api.selections + api.saves, 0);
    expect(controller.session!.selectedProfile, 'local');
    expect(controller.session!.id, 'demo');
  });

  testWidgets('loading updates to ready without replacing the device page', (
    tester,
  ) async {
    final api = InspectApi()..profileGate = Completer<List<String>>();
    final controller = DesktopSession(api);
    addTearDown(controller.dispose);
    final starting = controller.start();
    await mountDevice(tester, controller);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.text('Connecting'), findsOneWidget);
    expect(
      tester
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Refresh session'),
          )
          .onPressed,
      isNull,
    );
    api.profileGate!.complete(['local']);
    await starting;
    await tester.pumpAndSettle();
    expect(find.byType(LinearProgressIndicator), findsNothing);
    expect(find.text('Connected'), findsOneWidget);
    expect(find.textContaining('Demo device'), findsOneWidget);
  });

  testWidgets('failed connection stays offline and explicit retry recovers', (
    tester,
  ) async {
    final api = InspectApi(demo: false)..failConnection = true;
    final controller = DesktopSession(api);
    addTearDown(controller.dispose);
    await controller.start();
    await mountDevice(tester, controller);
    expect(find.text('Offline'), findsOneWidget);
    expect(find.text('Session needs attention'), findsOneWidget);
    expect(find.text('Read-only device view'), findsOneWidget);
    expect(api.creates, 0);
    api.failConnection = false;
    final refresh = find.widgetWithText(OutlinedButton, 'Refresh session');
    await tester.ensureVisible(refresh);
    await tester.tap(refresh);
    await tester.pumpAndSettle();
    expect(find.text('Connected'), findsOneWidget);
    expect(find.text('Session needs attention'), findsNothing);
    expect(api.creates, 1);
    expect(api.submissions, 0);
  });

  testWidgets(
    'busy session is inspectable and refresh does not submit a turn',
    (tester) async {
      final api = InspectApi(running: true);
      final controller = DesktopSession(api);
      addTearDown(controller.dispose);
      await controller.start();
      await mountDevice(tester, controller);
      expect(find.text('Turn in progress'), findsOneWidget);
      final refresh = find.widgetWithText(OutlinedButton, 'Refresh session');
      await tester.ensureVisible(refresh);
      await tester.tap(refresh);
      await tester.pumpAndSettle();
      expect(api.reads, 2);
      expect(api.creates, 1);
      expect(api.submissions, 0);
      expect(controller.session!.running, true);
    },
  );

  testWidgets('empty selection has no invented device or network operation', (
    tester,
  ) async {
    final api = InspectApi();
    final controller = DesktopSession(api);
    addTearDown(controller.dispose);
    await mountDevice(tester, controller, device: '');
    expect(find.text('No device selected'), findsOneWidget);
    expect(find.text('Connected'), findsNothing);
    expect(api.creates + api.reads, 0);
  });

  testWidgets('selecting another device resets the detail section', (
    tester,
  ) async {
    final api = InspectApi();
    final controller = DesktopSession(api);
    addTearDown(controller.dispose);
    await controller.start();
    await mountDevice(tester, controller);
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Device configuration'), findsOneWidget);
    await mountDevice(tester, controller, device: 'Laptop host');
    expect(find.text('Laptop host'), findsOneWidget);
    expect(find.text('Session service'), findsOneWidget);
    expect(find.text('Device configuration'), findsNothing);
    expect(api.creates, 1);
  });

  for (final brightness in Brightness.values) {
    testWidgets('narrow large-text device sections fit in $brightness', (
      tester,
    ) async {
      final controller = DesktopSession(InspectApi());
      addTearDown(controller.dispose);
      await controller.start();
      await mountDevice(
        tester,
        controller,
        device: 'A workstation with a long display name',
        size: const Size(360, 640),
        scale: 2,
        brightness: brightness,
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      final settings = find.text('Settings');
      await tester.ensureVisible(settings);
      await tester.tap(settings);
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Configure audio'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('device sections support keyboard activation', (tester) async {
    final controller = DesktopSession(InspectApi());
    addTearDown(controller.dispose);
    await controller.start();
    await mountDevice(tester, controller);
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.space);
    await tester.pumpAndSettle();
    expect(find.text('Device configuration'), findsOneWidget);
  });
}
