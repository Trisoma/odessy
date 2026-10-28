import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/devices/presentation/pairing_page.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';

class DiscoveryBackend implements SetupBackend {
  final requests = <SetupRequest>[];
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final discovered = request.parameters['action'] == 'discover';
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': discovered
          ? {
              'devices': [
                {
                  'name': 'My laptop',
                  'address': '192.168.1.9:7443',
                  'usable': true,
                  'advertised_fingerprint': 'a' * 64
                }
              ]
            }
          : {
              'peers': {'peers': [], 'grant_revision': 1}
            }
    }, request.id);
  }

  @override
  Future<void> close() async {}
  @override
  Future<void> cancel() async {}
}

void main() {
  testWidgets('nearby selection fills an address without trusting or granting',
      (tester) async {
    tester.view.physicalSize = const Size(1100, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final backend = DiscoveryBackend();
    final controller = MaintenanceController(backend);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: PairingPage(controller: controller))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Find nearby devices · unverified'));
    await tester.pumpAndSettle();
    expect(find.text('My laptop'), findsOneWidget);
    await tester.tap(find.text('Use this device'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
        find.text('Advanced manual enrollment'), 400,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text('Advanced manual enrollment'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('pair-address')))
            .controller!
            .text,
        '192.168.1.9:7443');
    expect(tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).value,
        isFalse);
    expect(controller.canApply, isFalse);
    expect(
        backend.requests.every((r) =>
            ['list', 'discover'].contains(r.parameters['action']) ||
            (r.parameters['action'] == 'short-code' &&
                r.parameters['command'] == 'status')),
        isTrue);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
