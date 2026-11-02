import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/maintenance_page.dart';
import 'setup_controller_test.dart' show Backend;

class PeerBackend implements SetupBackend {
  final requests = <SetupRequest>[];
  bool malformed = false;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final value = request.parameters['action'] == 'list'
        ? {
            'operation': 'list',
            'peers': {
              'grant_revision': 4,
              'peers': [
                {
                  'alias': 'workstation',
                  'installation_id': 'device-1',
                  'fingerprint_sha256': 'a' * 64,
                  'address': '127.0.0.1:7443',
                  'capability_mcp': true,
                  'capability_telemetry': malformed ? 'yes' : false,
                  'workspace': 'C:/work'
                }
              ]
            }
          }
        : {'plan_id': 'review-id'};
    return SetupReply.fromJson(
        {'id': request.id, 'status': 'ok', 'result': value}, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

void main() {
  testWidgets(
      'existing installer view exposes verified resources and review gate',
      (tester) async {
    final c = MaintenanceController(Backend());
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: MaintenancePage(controller: c, installer: true))));
    expect(find.byKey(const ValueKey('native-cache')), findsOneWidget);
    expect(find.textContaining('Windows x64 engine'), findsOneWidget);
    final confirm =
        find.widgetWithText(FilledButton, 'Confirm reviewed operation');
    expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
  });

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

  testWidgets('peer list shows inspected identity and selection grants nothing',
      (tester) async {
    final backend = PeerBackend();
    final c = MaintenanceController(backend);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: MaintenancePage(controller: c, pairing: true))));
    await tester.enterText(
        find.byKey(const ValueKey('native-link')), 'local-link');
    await tester.ensureVisible(find.text('Inspect enrolled peers'));
    await tester.tap(find.text('Inspect enrolled peers'));
    await tester.pumpAndSettle();
    expect(find.text('Telemetry grant: Not present'), findsOneWidget);
    expect(find.text('Execution grant: Present'), findsOneWidget);
    expect(find.text('Fingerprint: ${'a' * 64}'), findsOneWidget);
    await tester.ensureVisible(find.text('Use peer workstation'));
    await tester.tap(find.text('Use peer workstation'));
    await tester.pump();
    expect(backend.requests.length, 1);
    expect(c.fields['alias'], 'workstation');
    expect(c.canApply, isFalse);
    await c.review('pair:grant-telemetry');
    expect(backend.requests.last.parameters['expected_peer_fingerprint'],
        'a' * 64);
    expect(backend.requests.last.parameters['expected_peer_revision'], 4);
    expect(c.canApply, isTrue);
    await c.apply();
    expect(backend.requests.last.parameters['expected_peer_fingerprint'],
        'a' * 64);
    expect(c.peers, isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    c.dispose();
  });

  test('malformed peer inventory remains unavailable', () async {
    final backend = PeerBackend()..malformed = true;
    final c = MaintenanceController(backend);
    c.update({'link': 'local-link'});
    await c.inspect('pair:list');
    expect(c.peers, isNull);
    expect(c.error, isNotEmpty);
    c.dispose();
  });

  test('manual alias changes clear selection and link changes clear inspection',
      () async {
    final backend = PeerBackend();
    final c = MaintenanceController(backend);
    c.update({'link': 'local-link'});
    await c.inspect('pair:list');
    c.selectPeer(c.peers!.single);
    c.update({'link': 'local-link', 'alias': 'other'});
    expect(c.selectedPeer, isNull);
    expect(c.peers, isNotNull);
    await c.review('pair:grant-telemetry');
    expect(
        backend.requests.last.parameters
            .containsKey('expected_peer_fingerprint'),
        isFalse);
    c.update({'link': 'different-link', 'alias': 'other'});
    expect(c.peers, isNull);
    expect(c.canApply, isFalse);
    c.dispose();
  });
}
