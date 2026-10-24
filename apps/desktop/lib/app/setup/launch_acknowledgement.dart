import 'dart:math';

import 'package:odessy_client/odessy_client.dart';

/// Terminal hand-off states accepted by `package-launch-ack`.
const launchReady = 'ready';
const launchConfigurationRequired = 'configuration_required';
const launchFailed = 'failed';

/// Hand-off acknowledgement between setup and the installed application.
///
/// Process creation is not readiness. Setup closes only after the installed
/// window reports that it resolved its own installation and either reached the
/// coordinator ([launchReady]) or is showing the configuration-required state
/// ([launchConfigurationRequired]). A failure keeps setup visible.
///
/// Receipts belong to the backend and are bound to the active version, so an
/// obsolete or unknown identifier fails instead of being read as ready. Nothing
/// here writes files: setup and the installed application never share a path.
abstract class LaunchAcknowledgement {
  /// A launch identifier in the shape the backend accepts (32 lowercase hex).
  /// Generated here so the trusted adapter, not the helper, owns it.
  static String newId() {
    final random = Random.secure();
    return List.generate(32, (_) => random.nextInt(16).toRadixString(16)).join();
  }

  /// Reports the terminal state for [launchId]. Only [launchReady],
  /// [launchConfigurationRequired] and [launchFailed] are accepted.
  Future<void> acknowledge(String launchId, {required String state});

  /// Polls the receipt until it leaves `pending`. Returns the acknowledged state,
  /// or null when [timeout] elapsed first. Throws when the receipt is rejected.
  Future<String?> waitFor(
    String launchId, {
    Duration timeout = const Duration(seconds: 45),
    Duration interval = const Duration(milliseconds: 250),
  });
}

/// Reads and writes the backend-owned receipt through the JSON-line bridge.
class BackendLaunchAcknowledgement implements LaunchAcknowledgement {
  BackendLaunchAcknowledgement(this.backend, {required this.root});

  final SetupBackend backend;
  final String root;
  int _sequence = 0;

  /// Only the fields the helper whitelists for these actions are sent; unknown
  /// keys are rejected there rather than silently ignored.
  Future<Json> _call(Map<String, Object?> parameters) async {
    final reply = await backend.execute(SetupRequest(
      id: 'launch-${DateTime.now().microsecondsSinceEpoch}-${_sequence++}',
      operation: 'manage',
      selection: const {},
      parameters: parameters,
    ));
    if (!reply.ok) {
      throw StateError(reply.reason.isNotEmpty ? reply.reason : reply.error);
    }
    return reply.result;
  }

  @override
  Future<void> acknowledge(String launchId, {required String state}) => _call({
        'action': 'package-launch-ack',
        'root': root,
        'launch_id': launchId,
        'state': state,
      });

  @override
  Future<String?> waitFor(
    String launchId, {
    Duration timeout = const Duration(seconds: 45),
    Duration interval = const Duration(milliseconds: 250),
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      try {
        final result = await _call({
          'action': 'package-launch-status',
          'root': root,
          'launch_id': launchId,
        });
        final state = result['state'];
        if (state is String && state != 'pending') return state;
      } on StateError {
        // Unknown or obsolete identifier: never widen this into readiness.
        return launchFailed;
      }
      await Future<void>.delayed(interval);
    }
    return null;
  }
}
