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

/// The opt-in wake-word model: one pretrained file in its own directory, under a
/// different licence from the voice components.
final wakeArtifacts = <Map<String, Object>>[
  {
    'id': 'wake-model',
    'label': 'Wake word model (Hey Jarvis)',
    'kind': 'file',
    'field': 'wake_model',
    'file': 'hey_jarvis_v0.1.onnx',
    'size_bytes': 1271370,
    'license': 'CC-BY-NC-SA-4.0',
    'url': 'https://example.invalid/hey_jarvis_v0.1.onnx',
    'sha256': 'f' * 64,
  },
];

/// The wake word's own Python side. `openwakeword` is what makes the model
/// loadable, so it is not shared with the speech wheels.
final wakeWheels = <Map<String, Object>>[
  for (final name in ['numpy', 'onnxruntime', 'openwakeword'])
    {
      'id': 'wake-$name',
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

/// Where the runtime reads the wake-word model from.
const wakeRoot = r'C:\Users\me\AppData\Local\Odessy\wake';

/// A path inside the fake wake-word directory.
String wakePath(String file) => '$wakeRoot\\$file';

/// The artifacts a request selects, mirroring the helper's per-feature filter.
List<Map<String, Object>> featureEntries(Set<String> features) => [
      if (features.contains('voice')) ...voicePlanEntries,
      if (features.contains('wake')) ...[...wakeArtifacts, ...wakeWheels],
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
  bool wakeInstalled = false;
  bool wakeDependenciesInstalled = false;
  Completer<SetupReply>? pendingVoiceInstall;
  Completer<SetupReply>? pendingWakeInstall;
  String voiceInstallRoot = '';
  bool closed = false;
  bool statusTruncated = false;
  List<Json> statusLogs = const [];
  Json failure = const {};
  String failureReason = '';

  /// Actions that must fail; empty means [failureReason] applies to everything.
  Set<String> failActions = {};

  /// The features a request selected. The helper defaults to all of them, and so
  /// does this fake, so a request that forgets the key is still meaningful.
  static Set<String> featuresOf(SetupRequest request) {
    final raw = request.parameters['features'];
    if (raw is List && raw.isNotEmpty) return {for (final item in raw) '$item'};
    return {'voice', 'wake'};
  }

  /// Whether one artifact of the fake plan is already in place.
  bool artifactReady(Map<dynamic, dynamic> entry) =>
      '${entry['id']}'.startsWith('wake-') ? wakeInstalled : voiceInstalled;

  /// Whether the Python side of every selected feature was installed.
  bool dependenciesReady(Set<String> features) =>
      features.every((feature) => feature == 'wake'
          ? wakeDependenciesInstalled
          : voiceDependenciesInstalled);

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final action = request.parameters['action'] as String? ?? request.operation;
    final selected = featuresOf(request);
    if (action == 'voice-install') {
      final pending = selected.contains('wake') && !selected.contains('voice')
          ? pendingWakeInstall
          : pendingVoiceInstall;
      if (pending != null) return pending.future;
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
      'voice-plan' => () {
          final entries = featureEntries(selected);
          final outstanding = [
            for (final entry in entries)
              if (!artifactReady(entry)) entry
          ];
          return {
            'platform': 'windows',
            'architecture': 'x86_64',
            'root': voiceRoot,
            'wake_root': wakeRoot,
            'features': selected.toList()..sort(),
            'available': [
              for (final entry in entries)
                {...entry, 'installed': artifactReady(entry)}
            ],
            'unavailable': const [],
            'total_bytes': outstanding.fold<int>(
                0, (sum, entry) => sum + (entry['size_bytes'] as int)),
            'free_bytes': 64 * 1024 * 1024 * 1024,
            'space_warning': '',
            'nothing_to_do': outstanding.isEmpty,
            'requires_internet': outstanding.isNotEmpty,
            'installable': true,
            'manifest_path': r'C:\payload\voice\voice-artifacts.json',
            'plan_id': 'voice-1',
          };
        }(),
      'voice-status' => () {
          final entries = featureEntries(selected);
          Map<String, Object> component(Map<String, Object> entry) {
            final ready = artifactReady(entry);
            final wake = entry['field'] == 'wake_model';
            final label = entry['label'] as Object;
            final id = entry['id'] as Object;
            final field = entry['field'] as Object;
            return {
              'id': id,
              'label': label,
              'field': field,
              'path': wake
                  ? wakePath(entry['file'] as String)
                  : voicePath(voiceTarget(entry)),
              'state': ready ? 'ready' : 'missing',
              'severity': ready ? 'info' : 'warning',
              'check_id': 'setup/${wake ? 'wake' : 'voice'}/${entry['id']}',
              'message': ready
                  ? '$label is present and verified.'
                  : '$label is missing.',
              'fix_hint': ready
                  ? ''
                  : (wake
                      ? 'Download the wake-word model in setup, or skip it.'
                      : 'Download voice components, or select an existing file in setup.'),
            };
          }

          final components = [
            for (final entry in entries)
              if (entry['kind'] != 'wheel') component(entry)
          ];
          final missing = [
            for (final item in components)
              if (item['state'] != 'ready') item['id']
          ];
          final depsReady = dependenciesReady(selected);
          final wake = selected.contains('wake');
          final ready = missing.isEmpty && depsReady;
          return {
            'platform': 'windows',
            'root': voiceRoot,
            'wake_root': wakeRoot,
            'features': selected.toList()..sort(),
            'components': components,
            'missing': missing,
            'findings': [
              for (final item in components)
                {
                  'check_id': item['check_id'],
                  'severity': item['severity'],
                  'message': item['message'],
                  'path': item['path'],
                  'fix_hint': item['fix_hint'],
                }
            ],
            'severity': ready ? 'info' : 'warning',
            'ready': ready,
            'readiness': {
              for (final feature in selected) feature: ready,
            },
            'python': depsReady
                ? {
                    'check_id': 'setup/speech/python',
                    'severity': 'info',
                    'ready': true,
                    'message': wake && !selected.contains('voice')
                        ? 'The Python wake-word dependencies are installed and importable.'
                        : 'The Python voice dependencies are installed and importable.',
                    'fix_hint': '',
                  }
                : {
                    'check_id': 'setup/speech/python',
                    'severity': 'warning',
                    'ready': false,
                    'message':
                        'The Python speech dependencies are downloaded but not importable in the installed runtime.',
                    'fix_hint':
                        'Install speech dependencies for the installed Odessy runtime, or retry it.',
                  },
            'wake': wake
                ? {
                    'check_id': 'setup/wake',
                    'severity': wakeInstalled ? 'info' : 'warning',
                    'ready': wakeInstalled && wakeDependenciesInstalled,
                    'message': wakeInstalled
                        ? (wakeDependenciesInstalled
                            ? 'The wake word is ready.'
                            : 'The wake-word model is installed but openwakeword is not importable yet.')
                        : 'The wake-word model is not installed.',
                    'fix_hint': wakeInstalled && wakeDependenciesInstalled
                        ? ''
                        : 'Install the wake-word model and its dependencies in setup.',
                  }
                : const {},
            'progress': const {},
            'paths': {
              if (voiceInstalled)
                for (final entry in voiceArtifacts)
                  entry['field'] as String: voicePath(voiceTarget(entry)),
              if (wakeInstalled)
                for (final entry in wakeArtifacts)
                  entry['field'] as String: wakePath(entry['file'] as String),
            },
          };
        }(),
      'voice-install' => () {
          if (selected.contains('voice')) voiceInstalled = true;
          if (selected.contains('wake')) wakeInstalled = true;
          final entries = featureEntries(selected);
          return {
            'installed': entries,
            'preserved': const [],
            'cancelled': false,
            'state': 'ready',
            'features': selected.toList()..sort(),
            'missing': const [],
            'paths': {
              for (final entry in [...voiceArtifacts, ...wakeArtifacts])
                if (artifactReady(entry))
                  entry['field'] as String: entry['field'] == 'wake_model'
                      ? wakePath(entry['file'] as String)
                      : voicePath(voiceTarget(entry))
            },
            'total_bytes': entries.fold<int>(
                0, (sum, entry) => sum + (entry['size_bytes'] as int)),
            'attempted': true,
            'persisted': true,
            'verified': true,
            'severity': 'info',
            'wheel_count':
                entries.where((entry) => entry['kind'] == 'wheel').length,
            'manual_step': '',
          };
        }(),
      'voice-python-install' => () {
          if (selected.contains('voice')) voiceDependenciesInstalled = true;
          if (selected.contains('wake')) wakeDependenciesInstalled = true;
          voiceInstallRoot =
              request.parameters['install_root'] as String? ?? '';
          return {
            'installed': true,
            'verified': true,
            'severity': 'info',
            'python': voicePath('Scripts\\python.exe'),
            'wheels': featureEntries(selected)
                .where((entry) => entry['kind'] == 'wheel')
                .length,
            'features': selected.toList()..sort(),
            'log': [
              for (final feature in selected)
                feature == 'wake'
                    ? 'Successfully installed openwakeword'
                    : 'Successfully installed kokoro-onnx'
            ],
            'imports': 'ok',
            'failed_features': const [],
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
    if (pendingWakeInstall != null && !pendingWakeInstall!.isCompleted) {
      pendingWakeInstall!.completeError(StateError('cancelled'));
    }
  }

  @override
  Future<void> close() async => closed = true;
}
