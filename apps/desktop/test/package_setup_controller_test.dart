import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/package_setup_controller.dart';
import 'setup_controller_test.dart' show Backend;

class SingleFlightBackend extends Backend {
  final entered = Completer<void>();
  final release = Completer<void>();
  final actions = <String>[];
  bool active = false;

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (active) throw StateError('Setup helper is unavailable or busy.');
    active = true;
    actions.add(request.parameters['action'] as String);
    try {
      if (!entered.isCompleted) {
        entered.complete();
        await release.future;
      }
      return await super.execute(request);
    } finally {
      active = false;
    }
  }
}

void main() {
  test('background checks finish before an install review uses the helper',
      () async {
    final backend = SingleFlightBackend();
    final c = PackageSetupController(backend);
    final status = c.call('voice-status');
    await backend.entered.future;
    final review = c.run('package-review');
    await Future<void>.delayed(Duration.zero);
    expect(backend.actions, ['voice-status']);
    backend.release.complete();
    await status;
    await review;
    expect(c.error, isEmpty);
    expect(c.review['plan_id'], 'review-id');
    expect(backend.actions, ['voice-status', 'package-review']);
    await c.close();
    c.dispose();
  });

  test('cancelling auxiliary work discards queued operations and late replies',
      () async {
    final backend = SingleFlightBackend();
    final c = PackageSetupController(backend);
    final status = c.call('voice-status');
    final statusCheck = expectLater(status, throwsStateError);
    await backend.entered.future;
    final plan = c.call('voice-plan');
    final planCheck = expectLater(plan, throwsStateError);
    await c.cancel();
    backend.release.complete();
    await Future.wait([statusCheck, planCheck]);
    expect(backend.cancelled, isTrue);
    expect(backend.actions, ['voice-status']);
    await c.close();
    c.dispose();
  });

  test('packaged install requires reviewed exact inputs', () async {
    final backend = Backend();
    final c = PackageSetupController(backend);
    c.context = {'payload_root': 'C:/payload'};
    c.edit(destination: 'C:/target');
    await c.run('package-install');
    expect(backend.requests, isEmpty);
    await c.run('package-review');
    expect(c.review['plan_id'], 'review-id');
    c.edit(destination: 'C:/changed');
    expect(c.review, isEmpty);
    await c.run('package-review');
    await c.run('package-install');
    final install = backend.requests
        .singleWhere((r) => r.parameters['action'] == 'package-install');
    expect(install.parameters['confirm'], 'review-id');
    expect(install.parameters['payload'], 'C:/payload');
    expect(install.parameters['root'], 'C:/changed');
  });
}
