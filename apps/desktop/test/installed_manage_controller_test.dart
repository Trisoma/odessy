import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/installed_manage_controller.dart';

class Backend implements SetupBackend {
  final requests = <SetupRequest>[];
  String failure = '';
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
    final result = switch (action) {
      'installed-spec' => {
          'root': 'C:/Odessy/App',
          'role': 'combined',
          'model_configured': false,
          'workspace': 'C:/Users/me/Documents/Odessy',
          'payload': 'C:/Users/me/AppData/Local/OdessyMaintenance/id/hash',
        },
      'package-status' => status,
      'package-repair-review' => {'plan_id': 'repair-1', 'operation': 'repair'},
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
      'package-uninstall' => {'removed': ['owned'], 'retained': []},
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
