import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/devices/presentation/short_code_panel.dart';
import 'package:odessy_desktop/features/devices/controllers/pairing_controller.dart';

class PairingPreview implements PairingBackend {
  PairingPreview({this.supported = true});
  final bool supported;
  String? received;
  @override
  bool get isDemo => true;
  PairingSnapshot state(String value) => PairingSnapshot.fromJson({
        'schema_version': 1,
        'supported': supported,
        'state': supported ? value : 'closed',
        'reason': 'protocol_review_required',
        if (supported && value != 'closed') 'session_id': 'test-session'
      });
  @override
  Future<PairingSnapshot> status() async => state('closed');
  @override
  Future<PairingSnapshot> begin(String workspace) async => state('closed');
  @override
  Future<PairingSnapshot> join(String address, String code) async {
    received = code;
    return state('paired_no_access');
  }

  @override
  Future<PairingSnapshot> confirm(String id, String token) async =>
      state('ready');
  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

class FailedCancellation extends PairingPreview {
  @override
  Future<void> cancel() async {
    throw StateError('closed transport');
  }
}

void main() {
  test('failed cancellation reports uncertainty instead of success', () async {
    final controller = PairingController(FailedCancellation());
    await controller.cancel();
    expect(controller.message, isEmpty);
    expect(controller.error, contains('outcome unknown'));
    expect(controller.busy, isFalse);
    controller.dispose();
  });
  testWidgets(
      'fake preview preserves six digits and never says access was granted',
      (tester) async {
    final backend = PairingPreview();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ShortCodePanel(
                backend: backend, address: '192.168.1.2:7444'))));
    await tester.pumpAndSettle();
    expect(find.textContaining('Preview'), findsWidgets);
    await tester.enterText(
        find.byKey(const ValueKey('pairing-code')), '012345');
    await tester.pump();
    await tester.tap(find.text('Pair device'));
    await tester.pumpAndSettle();
    expect(backend.received, '012345');
    expect(find.textContaining('No execution access'), findsOneWidget);
    expect(find.text('012345'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'unreviewed native capability never exposes a working code action',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ShortCodePanel(
                backend: PairingPreview(supported: false),
                address: '192.168.1.2:7444'))));
    await tester.pumpAndSettle();
    expect(find.textContaining('security review'), findsOneWidget);
    expect(find.byKey(const ValueKey('pairing-code')), findsNothing);
    expect(find.text('Pair device'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
