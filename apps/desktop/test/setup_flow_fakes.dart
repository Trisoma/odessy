import 'package:odessy_client/odessy_client.dart';

/// A reviewed fake of the setup bridge. It never reports success for an action it
/// does not model, and it keeps the request log so tests can assert binding.
class FlowBackend implements SetupBackend {
  FlowBackend({this.launchResponse = const {'launched': true, 'pid': 42}});

  final Json launchResponse;
  final requests = <SetupRequest>[];
  bool installed = false;
  bool closed = false;
  bool statusTruncated = false;
  List<Json> statusLogs = const [];
  Json failure = const {};
  String failureReason = '';

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (failureReason.isNotEmpty) {
      return SetupReply.fromJson(
          {'id': request.id, 'status': 'error', 'reason': failureReason},
          request.id);
    }
    final action = request.parameters['action'] as String? ?? request.operation;
    final result = switch (action) {
      'context' => {
          'schema_version': 1,
          'launch_mode': installed ? 'manage' : 'setup',
          'root': request.parameters['root'] is String &&
                  (request.parameters['root'] as String).isNotEmpty
              ? request.parameters['root']
              : r'C:\Users\me\AppData\Local\Odessy\App',
          'installation_format': installed ? 'versioned' : 'new',
          'installation_id': installed ? 'a' * 32 : null,
          'payload_root': r'C:\payload',
          'components': ['python', 'desktop'],
          'bundle': r'C:\payload\wheelhouse',
          'verified': true,
        },
      'package-review' => {
          'plan_id': 'plan-1',
          'components': ['desktop', 'runtime'],
          'absent_components': ['node', 'tools'],
          'downloads': [],
          'readiness': 'needs_configuration',
        },
      'package-install' => () {
          installed = true;
          return {
            'installed': true,
            'active': 'v1',
            'registration': 'complete',
            'role': 'combined',
          };
        }(),
      'package-status' => {
          'installation_format': 'versioned',
          'active': 'v1',
          'versions': ['v1'],
          'stage': 'dependencies',
          'status': 'running',
          'steps_done': 3,
          'steps_total': 9,
          'logs': statusLogs,
          'log_cursor': statusLogs.isEmpty ? 0 : 2,
          'logs_truncated': statusTruncated,
          'registration': 'complete',
          'configuration': {
            'role': 'combined',
            'model_configured': false,
            'workspace': r'C:\Users\me\Documents\Odessy',
          },
        },
      'installed-spec' => {
          'root': r'C:\Users\me\AppData\Local\Odessy\App',
          'installation_id': 'a' * 32,
          'role': 'combined',
          'model_configured': false,
          'workspace': r'C:\Users\me\Documents\Odessy',
          'payload': r'C:\Users\me\AppData\Local\OdessyMaintenance\id\hash',
        },
      'package-configure-review' => {'plan_id': 'config-1'},
      'package-configure' => {'configured': true, 'model_configured': true},
      'package-uninstall-review' => {
          'plan_id': 'uninstall-1',
          'operation': 'uninstall',
          'keep_models': true,
          'keep_config': true,
        },
      'package-uninstall' => {'removed': ['owned'], 'retained': []},
      'package-launch' => launchResponse,
      _ => {'ok': true},
    };
    return SetupReply.fromJson(
        {'id': request.id, 'status': 'ok', 'result': result}, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async => closed = true;
}
