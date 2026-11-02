import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/maintenance_controller.dart';
import 'setup_controller_test.dart' show Backend;

void main() {
  for (final action in ['pair:revoke-telemetry', 'pair:revoke-execution']) {
    test('$action requires review and excludes the workspace', () async {
      final backend = Backend();
      final controller = MaintenanceController(backend);
      controller.update(
          {'link': 'local-link', 'alias': 'node', 'workspace': 'unrelated'});
      await controller.apply();
      expect(backend.requests, isEmpty);
      await controller.review(action);
      expect(backend.requests.single.parameters['review'], isTrue);
      expect(
          backend.requests.single.parameters.containsKey('workspace'), isFalse);
      await controller.apply();
      expect(backend.requests.last.parameters['action'], action.substring(5));
      expect(backend.requests.last.parameters['confirm'], 'review-id');
      expect(controller.needsInspection, isTrue);
      controller.dispose();
    });
  }
  test('telemetry grant needs review and does not include a workspace grant',
      () async {
    final backend = Backend();
    final controller = MaintenanceController(backend);
    controller.update(
        {'link': 'local-link', 'alias': 'node', 'workspace': 'unrelated'});
    await controller.apply();
    expect(backend.requests, isEmpty);
    await controller.review('pair:grant-telemetry');
    final review = backend.requests.single;
    expect(review.operation, 'pair');
    expect(review.parameters['action'], 'grant-telemetry');
    expect(review.parameters['review'], isTrue);
    expect(review.parameters.containsKey('workspace'), isFalse);
    await controller.apply();
    expect(backend.requests.last.parameters['action'], 'grant-telemetry');
    expect(backend.requests.last.parameters['confirm'], 'review-id');
    controller.dispose();
  });
  test('rollback and uninstall require their exact operation plans', () async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    c.update({'root': '/tmp/runtime', 'version': 'old'});
    await c.review('rollback');
    expect(backend.requests.last.parameters['action'], 'rollback-plan');
    c.update({'root': '/tmp/runtime'});
    expect(c.canApply, false);
    await c.review('uninstall');
    expect(backend.requests.last.parameters['action'], 'uninstall-plan');
    await c.apply();
    expect(backend.requests.last.parameters['action'], 'uninstall');
    expect(c.needsInspection, true);
    c.dispose();
  });

  test('installer UI reviews and dispatches original engine operation',
      () async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    c.update({'root': 'C:/Odessy', 'cache': 'C:/Cache', 'bundle': 'C:/Tools'});
    await c.review('engine:install');
    expect(backend.requests.last.operation, 'installer');
    expect(backend.requests.last.parameters['action'], 'review');
    await c.apply();
    expect(backend.requests.last.parameters['action'], 'install');
    expect(backend.requests.last.parameters['confirm'], 'review-id');
    c.dispose();
  });
  test(
      'installer resume review carries explicit resume without selection overrides',
      () async {
    final backend = Backend();
    final c = MaintenanceController(backend);
    c.update({'root': 'C:/Odessy', 'config': 'C:/selection.json'});
    await c.review('engine:resume');
    expect(backend.requests.last.parameters['resume'], true);
    expect(backend.requests.last.parameters.containsKey('config'), false);
    c.dispose();
  });

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
