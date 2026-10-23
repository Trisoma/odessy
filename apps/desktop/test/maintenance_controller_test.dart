import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';
import 'setup_controller_test.dart' show Backend;

void main() {
  test('repair reviews stored selection using repair-plan', () async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    c.update({'root': '/tmp/runtime', 'bundle': '/tmp/bundle'});
    await c.review('repair');
    expect(backend.requests.last.parameters['action'], 'repair-plan');
    c.dispose();
  });

  test(
      'mutation requires reviewed matching action and edits invalidate consent',
      () async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    c.update({'root': '/tmp/runtime', 'bundle': '/tmp/bundle'});
    await c.apply();
    expect(backend.requests, isEmpty);
    await c.review('install');
    expect(c.canApply, isTrue);
    c.update({'root': '/tmp/other'});
    expect(c.canApply, isFalse);
    c.dispose();
  });
  test('cancelled mutation requires inspection and ignores stale success',
      () async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    c.update({'root': '/tmp/runtime', 'bundle': '/tmp/bundle'});
    await c.review('install');
    backend.pending = Completer<SetupReply>();
    final operation = c.apply();
    await c.cancel();
    final request = backend.requests.last;
    backend.pending!.complete(SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'installed': true}
    }, request.id));
    await operation;
    expect(c.needsInspection, isTrue);
    expect(c.canApply, isFalse);
    c.dispose();
  });
}
