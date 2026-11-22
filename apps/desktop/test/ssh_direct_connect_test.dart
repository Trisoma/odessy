import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/execution_hosts_controller.dart';
import 'package:odessy_desktop/app/ssh_connection_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/execution_hosts_page.dart';
import 'saved_chat_navigation_test.dart'
    show capture, captureKey, loadCaptureFonts;
import 'ssh_connection_test.dart' show FakeSsh, FakeExecutionHosts;

Future<SshConnectionController> mount(WidgetTester t, FakeSsh api,
    {Size size = const Size(1100, 1000), bool dark = false}) async {
  t.view.physicalSize = size;
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.resetPhysicalSize);
  addTearDown(t.view.resetDevicePixelRatio);
  await loadCaptureFonts();
  final controller = SshConnectionController(api);
  final hosts = ExecutionHostsController(FakeExecutionHosts());
  await t.pumpWidget(RepaintBoundary(
      key: captureKey,
      child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: odessyTheme(dark ? Brightness.dark : Brightness.light),
          home: Scaffold(
              body: ExecutionHostsPage(controller: hosts, ssh: controller)))));
  await t.pumpAndSettle();
  addTearDown(() async {
    await t.pumpWidget(const SizedBox.shrink());
    // Controller is disposed in each test before Flutter verifies timers.
    hosts.dispose();
  });
  return controller;
}

Future<void> screenshot(WidgetTester t, String name) async {
  await t.pump(const Duration(milliseconds: 300));
  await capture(t, name);
}

void main() {
  testWidgets('visible address port password connect once and disconnect',
      (t) async {
    final api = FakeSsh();
    final c = await mount(t, api);
    expect(c.selected, isNull);
    expect(find.text('Review computer'), findsNothing);
    expect(find.text('Test connection'), findsNothing);
    expect(find.text('Save SSH computer'), findsNothing);
    await screenshot(t, '01-address-entry');
    await t.enterText(find.byKey(const Key('ssh-target')), 'jaimef@10.1.1.2');
    await t.enterText(find.byKey(const Key('ssh-port')), '2222');
    final password = find.byKey(const Key('ssh-password'));
    expect(t.widget<TextField>(password).obscureText, isTrue);
    await t.enterText(password, 'fixture-password');
    await screenshot(t, '02-optional-port-password');
    await t.tap(find.byKey(const Key('ssh-connect')));
    await t.pumpAndSettle();
    expect(api.receivedTarget, 'jaimef@10.1.1.2');
    expect(api.receivedPort, 2222);
    expect(api.receivedPassword, 'fixture-password');
    expect(t.widget<TextField>(password).controller!.text, isEmpty);
    expect(c.hasTarget, isTrue);
    expect(find.text('Session connection'), findsOneWidget);
    expect(find.text('Remove SSH computer'), findsNothing);
    await screenshot(t, '03-connected-read-only');
    await t.ensureVisible(find.text('Disconnect'));
    await t.tap(find.text('Disconnect'));
    await t.pumpAndSettle();
    expect(c.hasTarget, isFalse);
  });

  testWidgets('advanced alias fills the same field; edited target wins',
      (t) async {
    final api = FakeSsh();
    final c = await mount(t, api);
    final advanced = find.text('Advanced: SSH aliases and host identity');
    await t.ensureVisible(advanced);
    await t.tap(advanced);
    await t.pumpAndSettle();
    await t.ensureVisible(find.byType(DropdownButtonFormField<String>));
    await t.tap(find.byType(DropdownButtonFormField<String>));
    await t.pumpAndSettle();
    await t.tap(find.text('other').last);
    await t.pumpAndSettle();
    final address = find.byKey(const Key('ssh-target'));
    expect(t.widget<TextField>(address).controller!.text, 'other');
    await t.ensureVisible(address);
    await t.enterText(address, 'actual@192.0.2.10');
    await t.ensureVisible(find.byKey(const Key('ssh-connect')));
    await t.tap(find.byKey(const Key('ssh-connect')));
    await t.pumpAndSettle();
    expect(api.receivedTarget, 'actual@192.0.2.10');
    c.dispose();
  });

  for (final changed in [false, true]) {
    testWidgets('${changed ? 'changed' : 'unknown'} host stays disconnected',
        (t) async {
      final api = FakeSsh(known: changed, changed: changed);
      final c = await mount(t, api);
      await t.enterText(
          find.byKey(const Key('ssh-target')), 'tester@192.0.2.10');
      await t.tap(find.byKey(const Key('ssh-connect')));
      await t.pumpAndSettle();
      expect(c.hasTarget, isFalse);
      expect(c.error,
          contains(changed ? 'Host key changed' : 'not in known_hosts'));
      await capture(
          t, changed ? '05-changed-key-refused' : '04-unknown-key-refused');
    });
  }

  testWidgets('narrow dark keyboard submit and invalid port', (t) async {
    final api = FakeSsh();
    final c = await mount(t, api, size: const Size(480, 900), dark: true);
    await t.enterText(find.byKey(const Key('ssh-target')), 'tester@192.0.2.10');
    await t.enterText(find.byKey(const Key('ssh-port')), '99999');
    await t.tap(find.byKey(const Key('ssh-connect')));
    await t.pumpAndSettle();
    expect(api.receivedTarget, isNull);
    expect(find.textContaining('Port must be a number'), findsOneWidget);
    await t.enterText(find.byKey(const Key('ssh-port')), '22');
    await t.showKeyboard(find.byKey(const Key('ssh-password')));
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();
    expect(api.receivedTarget, 'tester@192.0.2.10');
    expect(api.receivedPassword, isEmpty);
    expect(t.takeException(), isNull);
    await screenshot(t, '06-narrow-dark');
    await t.sendKeyEvent(LogicalKeyboardKey.tab);
    await t.pump();
    expect(FocusManager.instance.primaryFocus, isNotNull);
    c.dispose();
  });

  testWidgets('busy connection protects inputs and cancellation is honest',
      (t) async {
    final api = DelayedConnect();
    final c = await mount(t, api);
    await t.enterText(find.byKey(const Key('ssh-target')), 'tester@192.0.2.10');
    await t.tap(find.byKey(const Key('ssh-connect')));
    await t.pump();
    expect(c.busy, isTrue);
    expect(t.widget<TextField>(find.byKey(const Key('ssh-target'))).enabled,
        isFalse);
    expect(
        t.widget<FilledButton>(find.byKey(const Key('ssh-connect'))).onPressed,
        isNull);
    await screenshot(t, '07-connecting');
    await t.tap(find.text('Stop waiting'));
    await t.pump();
    expect(c.message, contains('may finish connecting'));
    api.gate.complete({
      'connected': true,
      'target': {'alias': 'old'}
    });
    await t.pumpAndSettle();
    expect(c.hasTarget, isFalse);
  });

  test('failed new connect keeps existing target and status', () async {
    final api = FakeSsh();
    final c = SshConnectionController(api);
    await c.connect('first');
    final previous = c.target;
    api.changed = true;
    await c.connect('second');
    expect(c.target, same(previous));
    expect(c.status?['readiness'], 'ready');
    c.dispose();
  });
}

class DelayedConnect extends FakeSsh {
  final gate = Completer<Json>();
  @override
  Future<Json> connectSsh(String target, {int? port, String password = ''}) =>
      gate.future;
}
