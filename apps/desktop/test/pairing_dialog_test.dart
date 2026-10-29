import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';

const request = 'Incoming connection request';

Future<void> _open(WidgetTester tester, PairingTransport api) async {
  tester.view.physicalSize = const Size(1200, 850);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(OdessyApp(api: api));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('a LAN pairing request asks before it is trusted',
      (tester) async {
    final api = PairingTransport();
    await _open(tester, api);
    api.emit(SessionEvent(
        1, 'pairing_request', {'id': 'a1', 'name': 'Jetson', 'ip': '192.168.1.50'}));
    await tester.pumpAndSettle();
    expect(find.text(request), findsOneWidget);
    expect(
        find.textContaining(
            'Jetson (192.168.1.50) wants to connect to this computer as your coordinator.'),
        findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Accept'));
    await tester.pumpAndSettle();
    expect(api.answers, {'a1': true});
    expect(find.text(request), findsNothing);
  });

  testWidgets('declining tells the waiting coordinator no', (tester) async {
    final api = PairingTransport();
    await _open(tester, api);
    api.emit(SessionEvent(
        1, 'pairing_request', {'id': 'a1', 'name': 'Jetson', 'ip': '192.168.1.50'}));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, 'Decline'));
    await tester.pumpAndSettle();
    expect(api.answers, {'a1': false});
    expect(find.text(request), findsNothing);
  });

  testWidgets('a request answered elsewhere closes the prompt without answering',
      (tester) async {
    final api = PairingTransport();
    await _open(tester, api);
    api.emit(SessionEvent(
        1, 'pairing_request', {'id': 'a1', 'name': 'Jetson', 'ip': '192.168.1.50'}));
    await tester.pumpAndSettle();
    expect(find.text(request), findsOneWidget);
    api.emit(SessionEvent(2, 'pairing_resolved', {'id': 'a1', 'accepted': true}));
    await tester.pumpAndSettle();
    expect(find.text(request), findsNothing);
    expect(api.answers, isEmpty,
        reason: 'the second window must not answer an already resolved request');
    // The page underneath the dialog is still there, not an empty navigator.
    expect(find.widgetWithText(ListTile, 'Settings'), findsOneWidget);
  });

  testWidgets('a second request is not stacked behind the open prompt',
      (tester) async {
    final api = PairingTransport();
    await _open(tester, api);
    api.emit(SessionEvent(
        1, 'pairing_request', {'id': 'a1', 'name': 'Jetson', 'ip': '192.168.1.50'}));
    await tester.pumpAndSettle();
    api.emit(SessionEvent(
        2, 'pairing_request', {'id': 'a1', 'name': 'Jetson', 'ip': '192.168.1.50'}));
    await tester.pumpAndSettle();
    expect(find.text(request), findsOneWidget);
  });
}

/// A loopback transport that can raise pairing events on demand.
class PairingTransport extends DemoApi implements PairingApi {
  PairingTransport() : super(delay: const Duration(milliseconds: 20));
  final _events = StreamController<SessionEvent>.broadcast();
  final answers = <String, bool>{};

  void emit(SessionEvent event) => _events.add(event);

  @override
  Stream<SessionEvent> events(String sid, int after) => _events.stream;

  @override
  Future<void> approvePairing(String id, bool accepted) async {
    answers[id] = accepted;
  }

  @override
  void close() {
    unawaited(_events.close());
    super.close();
  }
}
