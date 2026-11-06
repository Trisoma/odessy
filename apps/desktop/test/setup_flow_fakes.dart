import 'dart:async';
import 'package:odessy_client/odessy_client.dart';

/// The four components a verified voice download would provide, in the order the
/// installer offers them.
/// The four components a verified voice download would provide, in the order the
/// installer offers them. `whisper-cli` arrives in an archive, as it does in the
/// real manifest.
final voiceArtifacts = <Map<String, Object>>[
  {
    'id': 'whisper-cli',
    'label': 'Whisper.cpp executable',
    'kind': 'archive',
    'field': 'whisper_bin',
    'file': 'whisper-bin-x64.zip',
    'size_bytes': 1048576,
    'license': 'MIT',
    'url': 'https://example.invalid/whisper-bin-x64.zip',
    'sha256': 'a' * 64,
  },
  {
    'id': 'whisper-model',
    'label': 'Whisper model (ggml-base.en)',
    'kind': 'file',
    'field': 'whisper_model',
    'file': 'ggml-base.en.bin',
    'size_bytes': 1048576,
    'license': 'MIT',
    'url': 'https://example.invalid/ggml-base.en.bin',
    'sha256': 'b' * 64,
  },
  {
    'id': 'kokoro-model',
    'label': 'Kokoro model (int8)',
    'kind': 'file',
    'field': 'kokoro_model',
    'file': 'kokoro-v1.0.int8.onnx',
    'size_bytes': 1048576,
    'license': 'Apache-2.0',
    'url': 'https://example.invalid/kokoro-v1.0.int8.onnx',
    'sha256': 'c' * 64,
  },
  {
    'id': 'kokoro-voices',
    'label': 'Kokoro voices',
    'kind': 'file',
    'field': 'kokoro_voices',
    'file': 'voices-v1.0.bin',
    'size_bytes': 1048576,
    'license': 'Apache-2.0',
    'url': 'https://example.invalid/voices-v1.0.bin',
    'sha256': 'd' * 64,
  },
];

/// The Python side, bundled with the components.
final voiceWheels = <Map<String, Object>>[
  for (final name in [
    'numpy',
    'onnxruntime',
    'soundfile',
    'sounddevice',
    'kokoro_onnx'
  ])
    {
      'id': 'py-$name',
      'label': '$name 1.0',
      'kind': 'wheel',
      'field': '',
      'file': '${name.replaceAll('_', '-')}-1.0-py3-none-any.whl',
      'size_bytes': 262144,
      'license': 'MIT',
      'url': 'https://example.invalid/$name.whl',
      'sha256': 'e' * 64,
    },
];

/// The user-owned voice directory the helper reports.
const voiceRoot = r'C:\Users\me\AppData\Local\Odessy\Voice';

/// A path inside the fake voice root.
String voicePath(String file) => '$voiceRoot\\$file';

/// The file an artifact ends up as: an archive produces its executable.
String voiceTarget(Map<dynamic, dynamic> entry) =>
    entry['kind'] == 'archive' ? 'whisper-cli.exe' : entry['file'] as String;

final voicePlanEntries = <Map<String, Object>>[
  ...voiceArtifacts,
  ...voiceWheels
];

/// A reviewed fake of the setup bridge. It never reports success for an action it
/// does not model, and it keeps the request log so tests can assert binding.
class FlowBackend implements SetupBackend {
  FlowBackend({this.launchResponse = const {'launched': true, 'pid': 42}});

  final Json launchResponse;
  final requests = <SetupRequest>[];
  bool installed = false;
  bool voiceInstalled = false;
  bool voiceDependenciesInstalled = false;
  Completer<SetupReply>? pendingVoiceInstall;
  String voiceInstallRoot = '';
  bool closed = false;
  bool statusTruncated = false;
  List<Json> statusLogs = const [];
  Json failure = const {};
  String failureReason = '';

  /// Actions that must fail; empty means [failureReason] applies to everything.
  Set<String> failActions = {};

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final action = request.parameters['action'] as String? ?? request.operation;
    if (action == 'voice-install' && pendingVoiceInstall != null) {
      return pendingVoiceInstall!.future;
    }
    if (failureReason.isNotEmpty &&
        (failActions.isEmpty || failActions.contains(action))) {
      return SetupReply.fromJson(
          {'id': request.id, 'status': 'error', 'reason': failureReason},
          request.id);
    }
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
            'active': 'a' * 32,
            'registration': 'complete',
            'role': 'combined',
          };
        }(),
      'package-status' => {
          'installation_format': 'versioned',
          'active': 'a' * 32,
          'versions': ['a' * 32],
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
      'package-uninstall' => {
          'removed': ['owned'],
          'retained': []
        },
      'package-launch' => launchResponse,
      'voice-plan' => {
          'platform': 'windows',
          'architecture': 'x86_64',
          'root': voiceRoot,
          'available': [
            for (final entry in voicePlanEntries)
              {...entry, 'installed': voiceInstalled}
          ],
          'unavailable': const [],
          'total_bytes': voiceInstalled ? 0 : 4 * 1048576 + 5 * 262144,
          'free_bytes': 64 * 1024 * 1024 * 1024,
          'space_warning': '',
          'nothing_to_do': voiceInstalled,
          'requires_internet': !voiceInstalled,
          'installable': true,
          'manifest_path': r'C:\payload\voice\voice-artifacts.json',
          'plan_id': 'voice-1',
        },
      'voice-status' => {
          'platform': 'windows',
          'root': voiceRoot,
          'components': [
            for (final entry in voiceArtifacts)
              {
                'id': entry['id'],
                'label': entry['label'],
                'field': entry['field'],
                'path': voicePath(voiceTarget(entry)),
                'state': voiceInstalled ? 'ready' : 'missing',
                'severity': voiceInstalled ? 'info' : 'warning',
                'check_id': 'setup/voice/${entry['id']}',
                'message': voiceInstalled
                    ? '${entry['label']} is present and verified.'
                    : '${entry['label']} is missing.',
                'fix_hint': voiceInstalled
                    ? ''
                    : 'Download voice components, or select an existing file in setup.',
              }
          ],
          'missing': voiceInstalled
              ? const []
              : [for (final entry in voiceArtifacts) entry['id']],
          'findings': [
            for (final entry in voiceArtifacts)
              {
                'check_id': 'setup/voice/${entry['id']}',
                'severity': voiceInstalled ? 'info' : 'warning',
                'message': voiceInstalled
                    ? '${entry['label']} is present and verified.'
                    : '${entry['label']} is missing.',
                'path': voicePath(voiceTarget(entry)),
                'fix_hint': voiceInstalled
                    ? ''
                    : 'Download voice components, or select an existing file in setup.',
              }
          ],
          'severity': voiceDependenciesInstalled
              ? 'info'
              : (voiceInstalled ? 'warning' : 'warning'),
          'ready': voiceInstalled && voiceDependenciesInstalled,
          'python': voiceDependenciesInstalled
              ? const {
                  'check_id': 'setup/voice/python',
                  'severity': 'info',
                  'message':
                      'The Python voice dependencies are installed and importable.',
                  'fix_hint': '',
                }
              : const {
                  'check_id': 'setup/voice/python',
                  'severity': 'warning',
                  'message':
                      'The Python voice dependencies are downloaded but not importable in the installed runtime.',
                  'fix_hint':
                      'Install voice dependencies for the installed Odessy runtime, or retry it.',
                },
          'progress': const {},
          'paths': const {},
        },
      'voice-install' => () {
          voiceInstalled = true;
          return {
            'installed': voicePlanEntries,
            'preserved': const [],
            'cancelled': false,
            'state': 'ready',
            'missing': const [],
            'paths': {
              for (final entry in voiceArtifacts)
                entry['field'] as String: voicePath(voiceTarget(entry))
            },
            'total_bytes': 4 * 1048576 + 5 * 262144,
            'attempted': true,
            'persisted': true,
            'verified': true,
            'severity': 'info',
            'wheel_count': voiceWheels.length,
            'manual_step': '',
          };
        }(),
      'voice-python-install' => () {
          voiceDependenciesInstalled = true;
          voiceInstallRoot =
              request.parameters['install_root'] as String? ?? '';
          return {
            'installed': true,
            'verified': true,
            'severity': 'info',
            'python': voicePath('Scripts\\python.exe'),
            'wheels': voiceWheels.length,
            'log': const ['Successfully installed kokoro-onnx'],
            'imports': 'ok',
            'manual_step': '',
          };
        }(),
      _ => {'ok': true},
    };
    return SetupReply.fromJson(
        {'id': request.id, 'status': 'ok', 'result': result}, request.id);
  }

  @override
  Future<void> cancel() async {
    if (pendingVoiceInstall != null && !pendingVoiceInstall!.isCompleted) {
      pendingVoiceInstall!.completeError(StateError('cancelled'));
    }
  }

  @override
  Future<void> close() async => closed = true;
}
