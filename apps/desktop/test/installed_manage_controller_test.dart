import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/installed_manage_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/manage_page.dart';

class Backend implements SetupBackend {
  final requests = <SetupRequest>[];
  String failure = '';
  bool damaged = false;
  String? installedPayload =
      'C:/Users/me/AppData/Local/OdessyMaintenance/id/hash';
  Json status = {
    'installation_format': 'versioned',
    'active': 'a' * 32,
    'versions': ['a' * 32, 'c' * 32],
    'registration': 'complete',
    'service_recorded': false,
    'configuration': {
      'role': 'combined',
      'model_configured': false,
      'workspace': 'C:/Users/me/Documents/Odessy',
    },
  };

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (failure.isNotEmpty) {
      return SetupReply.fromJson({
        'id': request.id,
        'status': 'error',
        'reason': failure,
      }, request.id);
    }
    final action = request.parameters['action'] as String;
    if (action == 'installed-spec' && damaged) {
      return SetupReply.fromJson({
        'id': request.id,
        'status': 'error',
        'reason': 'installed executable is damaged'
      }, request.id);
    }
    final result = switch (action) {
      'installed-spec' => {
          'root': 'C:/Odessy/App',
          'role': 'combined',
          'model_configured': false,
          'workspace': 'C:/Users/me/Documents/Odessy',
          'payload': installedPayload,
        },
      'package-status' => status,
      'package-repair-review' => {'plan_id': 'repair-1', 'operation': 'repair'},
      'package-configure-review' => {'plan_id': 'configure-1'},
      'package-rollback-review' => {
          'plan_id': 'rollback-1',
          'operation': 'rollback',
          'target_version': 'c' * 32,
        },
      'package-uninstall-review' => {
          'plan_id': 'uninstall-1',
          'operation': 'uninstall',
          'keep_models': true,
        },
      'package-uninstall' => {
          'removed': ['owned'],
          'retained': []
        },
      _ => {'done': true},
    };
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': result,
    }, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

InstalledManageController controller(Backend backend) =>
    InstalledManageController(backend, spec: {'root': 'C:/Odessy/App'});

void main() {
  test('failed installed spec preserves portable installation type', () async {
    final backend = Backend()..damaged = true;
    final c = InstalledManageController(backend,
        spec: {'root': 'C:/Odessy/App', 'portable': true});
    await c.load();
    expect(c.spec['portable'], isTrue);
    expect(c.error, contains('damaged'));
    c.dispose();
  });

  testWidgets('portable recovery keeps unavailable controls disabled',
      (tester) async {
    final backend = Backend()..damaged = true;
    final c = InstalledManageController(backend,
        spec: {'root': 'C:/Odessy/App', 'portable': true});
    await c.load();
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ManagePage(controller: c))));
    final service = tester.widget<OutlinedButton>(find.ancestor(
        of: find.text('Service controls'),
        matching: find.byType(OutlinedButton)));
    expect(service.onPressed, isNull);
    expect(find.textContaining('Portable build: close Odessy'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });

  test('damaged installation uses current recorded maintenance payload',
      () async {
    final backend = Backend()..damaged = true;
    backend.status['payload'] = 'C:/current-payload';
    final c =
        InstalledManageController(backend, spec: {'root': 'C:/Odessy/App'});
    await c.load();
    expect(c.error, contains('damaged'));
    expect(c.payload, 'C:/current-payload');
    await c.reviewRepair();
    expect(backend.requests.last.parameters['payload'], 'C:/current-payload');
    c.dispose();
  });

  test('status refresh prefers the current registered payload', () async {
    final backend = Backend()..installedPayload = 'C:/old-payload';
    backend.status['payload'] = 'C:/old-payload';
    final c = controller(backend);
    await c.load();
    backend.status = {...backend.status, 'payload': 'C:/updated-payload'};
    await c.refreshStatus();
    expect(c.payload, 'C:/updated-payload');
    c.dispose();
  });
  test('installed payload follows the latest spec after configuration',
      () async {
    final backend = Backend()..installedPayload = 'C:/current-payload';
    final c = InstalledManageController(backend,
        spec: {'root': 'C:/Odessy/App', 'payload': 'C:/old-payload'});
    await c.load();
    expect(c.payload, 'C:/current-payload');
    backend.installedPayload = 'C:/updated-payload';
    await c.reviewConfiguration({'role': 'combined'}, 'C:/work');
    await c.confirm();
    expect(c.payload, 'C:/updated-payload');
    await c.reviewRepair();
    expect(backend.requests.last.parameters['payload'], 'C:/updated-payload');
    await c.register();
    expect(
        backend.requests
            .lastWhere((r) => r.parameters['action'] == 'package-register')
            .parameters['payload'],
        'C:/updated-payload');
    c.dispose();
  });

  for (final cached in [null, 'C:/old-payload']) {
    test('explicit payload survives reload with cached payload $cached',
        () async {
      final backend = Backend()..installedPayload = cached;
      final c = InstalledManageController(backend,
          spec: {'root': 'C:/Odessy/App'}, setupPayload: 'C:/new-payload');
      await c.load();
      expect(c.payload, 'C:/new-payload');
      await c.reviewRepair();
      expect(backend.requests.last.parameters['payload'], 'C:/new-payload');
      await c.register();
      expect(
          backend.requests
              .lastWhere((r) => r.parameters['action'] == 'package-register')
              .parameters['payload'],
          'C:/new-payload');
      await c.reviewConfiguration({'role': 'combined'}, 'C:/work');
      await c.confirm();
      expect(c.payload, 'C:/new-payload');
      c.dispose();
    });
  }
  test('load reads the installed spec and journal status', () async {
    final backend = Backend();
    final c = controller(backend);
    await c.load();
    expect(c.activeVersion, 'a' * 32);
    expect(c.versions.length, 2);
    expect(c.configuration['role'], 'combined');
    expect(c.root, 'C:/Odessy/App');
    expect(c.payload, endsWith('/hash'));
    expect(c.busy, isFalse);
  });

  test('repair requires an exact review before the single mutation', () async {
    final backend = Backend();
    final c = controller(backend);
    await c.load();
    await c.confirm();
    expect(backend.requests.map((r) => r.parameters['action']),
        isNot(contains('package-repair')));
    await c.reviewRepair();
    expect(c.canConfirm, isTrue);
    await c.confirm();
    final mutation = backend.requests.firstWhere(
        (request) => request.parameters['action'] == 'package-repair');
    expect(mutation.parameters['confirm'], 'repair-1');
    expect(c.canConfirm, isFalse);
  });

  test('cancelling a review discards its confirmation', () async {
    final backend = Backend();
    final c = controller(backend);
    await c.load();
    await c.reviewRollback('c' * 32);
    expect(c.canConfirm, isTrue);
    c.cancelReview();
    expect(c.canConfirm, isFalse);
    await c.confirm();
    expect(backend.requests.map((r) => r.parameters['action']),
        isNot(contains('package-rollback')));
  });

  test('configuration review carries the selected model and workspace',
      () async {
    final backend = Backend();
    final c = controller(backend);
    await c.load();
    await c.reviewConfiguration({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': 'llama3'}
    }, 'C:/work');
    final request = backend.requests.last;
    expect(request.parameters['action'], 'package-configure-review');
    expect((request.parameters['selection'] as Json)['role'], 'combined');
    expect(request.parameters['workspace'], 'C:/work');
  });

  test('uninstall disables every later action', () async {
    final backend = Backend();
    final c = controller(backend);
    await c.load();
    await c.reviewUninstall();
    await c.confirm();
    expect(c.uninstalled, isTrue);
    expect(c.notice, contains('preserved'));
    final count = backend.requests.length;
    await c.verify();
    await c.reviewRepair();
    await c.launch();
    expect(backend.requests.length, count);
  });

  test('a helper reason is shown and the operation is not retried', () async {
    final backend = Backend()..failure = 'the installation is busy';
    final c = controller(backend);
    await c.load();
    expect(c.error, contains('busy'));
    expect(c.notice, contains('No automatic retry'));
    expect(c.busy, isFalse);
  });
}
