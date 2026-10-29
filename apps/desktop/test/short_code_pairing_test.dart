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

/// A backend whose state advances the way a real window does: `status` reports the
/// current state, and the test moves it on the way the other computer would.
class ScriptedBackend implements PairingBackend {
  ScriptedBackend(this.afterJoin, {this.current = 'closed'});
  final String afterJoin;
  String current;
  String? received;
  int statusCalls = 0;
  @override
  bool get isDemo => true;
  PairingSnapshot _state(String value) => PairingSnapshot.fromJson({
        'schema_version': 1,
        'supported': true,
        'state': value,
        'reason': '',
        'session_id': 'test-session',
        'review_token': '',
        'seconds_remaining': 90,
        'attempts_remaining': 2,
        if (value == 'awaiting_approval')
          'peer': {
            'alias': 'peer-jetson',
            'installation_id': 'jetson0001',
            'fingerprint_sha256': 'ab' * 32
          },
        if (value == 'rejected') 'message': 'the pairing code did not match',
      });
  @override
  Future<PairingSnapshot> status() async {
    statusCalls++;
    return _state(current);
  }

  @override
  Future<PairingSnapshot> begin(String workspace) async {
    current = 'discoverable';
    return _state(current);
  }

  @override
  Future<PairingSnapshot> join(String address, String code) async {
    received = code;
    current = afterJoin;
    return _state(current);
  }

  @override
  Future<PairingSnapshot> confirm(String id, String token) async {
    current = 'ready';
    return _state(current);
  }

  @override
  Future<void> cancel() async {
    current = 'cancelled';
  }

  @override
  Future<void> close() async {}
}

Future<void> _panel(WidgetTester tester, PairingBackend backend,
    {VoidCallback? onPaired}) async {
  await tester.pumpWidget(MaterialApp(
      home: Scaffold(
          body: ShortCodePanel(
              backend: backend,
              address: '192.168.1.2:7444',
              onPaired: onPaired))));
  await tester.pumpAndSettle();
}

Future<void> _pair(WidgetTester tester, String code) async {
  await tester.enterText(find.byKey(const ValueKey('pairing-code')), code);
  await tester.pump();
  await tester.tap(find.text('Pair device'));
  await tester.pumpAndSettle();
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

  testWidgets('a rejected code stays in the field and leaves retries visible',
      (tester) async {
    final backend = ScriptedBackend('rejected');
    await _panel(tester, backend);
    await _pair(tester, '012345');
    expect(backend.received, '012345');
    expect(find.textContaining('was not accepted'), findsOneWidget);
    expect(find.textContaining('Attempts left in this window: 2'), findsOneWidget);
    // The field keeps the digits: a rejected attempt is not a reason to retype.
    expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('pairing-code')))
            .controller!
            .text,
        '012345');
    expect(find.text('Pair device'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('an expired window says so and offers a fresh code entry',
      (tester) async {
    final backend = ScriptedBackend('paired_no_access', current: 'expired');
    await _panel(tester, backend);
    expect(find.textContaining('code expired'), findsOneWidget);
    expect(find.byKey(const ValueKey('pairing-code')), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('an incomplete attempt asks for inspection instead of a retry',
      (tester) async {
    final backend =
        ScriptedBackend('paired_no_access', current: 'incomplete');
    await _panel(tester, backend);
    expect(find.textContaining('outcome was known'), findsOneWidget);
    expect(find.byKey(const ValueKey('pairing-code')), findsNothing);
    expect(find.text('Pair device'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'waiting for the other computer polls status and reports the enrollment once',
      (tester) async {
    final backend = ScriptedBackend('awaiting_approval');
    var paired = 0;
    await _panel(tester, backend, onPaired: () => paired++);
    await _pair(tester, '042137');
    expect(backend.received, '042137');
    // ignore: avoid_print
    print(tester.allWidgets.whereType<Text>().map((t) => t.data).toList());
    final stateText = find.byKey(const ValueKey('pairing-state'));
    expect(stateText, findsOneWidget,
        reason: 'the panel must state what the window is doing');
    expect(tester.widget<Text>(stateText).data,
        contains('Waiting for the other computer'));
    expect(find.textContaining('peer-jetson'), findsOneWidget);
    expect(find.textContaining('042137'), findsNothing);
    final polls = backend.statusCalls;
    // The other computer approves while this screen is open.
    backend.current = 'paired_no_access';
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(backend.statusCalls, greaterThan(polls));
    expect(paired, 1, reason: 'the page is told once, not on every poll');
    expect(find.textContaining('No execution access was granted'), findsOneWidget);
    // Polling stops once the request is answered.
    final settled = backend.statusCalls;
    await tester.pump(const Duration(seconds: 4));
    expect(backend.statusCalls, settled);
    await tester.pumpWidget(const SizedBox());
  });
}
