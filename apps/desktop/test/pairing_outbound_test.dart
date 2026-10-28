import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/devices/presentation/pairing_page.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';

class ConsentBackend implements SetupBackend {
  final calls = <SetupRequest>[];
  bool enabled = false;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    calls.add(request);
    final action = request.parameters['action'];
    final result = action == 'outbound-review'
        ? {
            'plan_id': 'exact-review',
            'revision': 'before',
            'operation': 'outbound-consent',
            'arguments': ['Enable laptop outbound only'],
            'alias': 'laptop',
            'enabled': true,
          }
        : action == 'outbound-save'
            ? {'saved': true, 'enabled': enabled = true}
            : {
                'outbound': {'alias': 'laptop', 'enabled': enabled},
                'peers': {
                  'grant_revision': 2,
                  'peers': [
                    {
                      'alias': 'laptop',
                      'revoked': false,
                      'fingerprint_sha256': 'a' * 64,
                      'capability_mcp': false
                    }
                  ]
                }
              };
    return SetupReply.fromJson(
        {'id': request.id, 'status': 'ok', 'result': result}, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

void main() {
  testWidgets('outbound consent reviews then saves without an incoming grant',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final backend = ConsentBackend();
    final c = MaintenanceController(backend);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: PairingPage(controller: c))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Use for tools'));
    await tester.pumpAndSettle();
    expect(c.reviewedAction, 'pair:outbound-save');
    await tester.ensureVisible(find.text('Confirm reviewed operation'));
    await tester.tap(find.text('Confirm reviewed operation'));
    await tester.pumpAndSettle();
    final saved = backend.calls
        .singleWhere((r) => r.parameters['action'] == 'outbound-save');
    expect(saved.parameters['confirm'], 'exact-review');
    expect(saved.parameters['revision'], 'before');
    expect(saved.parameters['enabled'], true);
    expect(backend.calls.any((r) => r.parameters['action'] == 'grant'), false);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });
}
