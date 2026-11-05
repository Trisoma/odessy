import 'models.dart';

/// The two directions a handoff run can take, in the service's own names.
const handoffFlows = {'document_to_code', 'code_to_review'};

/// Run states that can still change. Every other state is final.
const activeRunStates = {
  'running_step_1',
  'awaiting_handoff',
  'handing_off',
  'running_step_2',
  'cancelling',
};

final _runId = RegExp(r'^[a-f0-9]{32}$');
final _sha256 = RegExp(r'^[a-f0-9]{64}$');

bool isRunId(Object? value) => value is String && _runId.hasMatch(value);
bool isArtifactSha256(Object? value) =>
    value is String && _sha256.hasMatch(value);

Never _invalid(String what) =>
    throw FormatException('Invalid handoff run: $what');

bool _strings(Object? value) =>
    value is List && value.every((e) => e is String);

void _checkArtifact(Object? value, {bool text = false}) {
  if (value is! Map ||
      value['kind'] is! String ||
      !isArtifactSha256(value['sha256']) ||
      value['bytes'] is! int ||
      (value['bytes'] as int) < 0 ||
      value['preview'] is! String ||
      !_strings(value['changed_files'] ?? const []) ||
      (text && value['text'] is! String)) {
    _invalid('artifact');
  }
}

/// Validates one run snapshot, from `GET .../runs/{id}`, a run mutation, or the
/// data of a `run` session event (which also carries `type`).
///
/// The panel draws `steps` as nodes and `transfers` as the edges between them,
/// so a snapshot that cannot be drawn is rejected rather than half shown.
Json parseRunSnapshot(Object? value) {
  if (value is! Map) _invalid('not an object');
  if (!isRunId(value['run_id'])) _invalid('run_id');
  if (!handoffFlows.contains(value['flow'])) _invalid('flow');
  if (value['state'] is! String) _invalid('state');
  if (value['error'] != null && value['error'] is! String) _invalid('error');
  if (value['worktree'] != null && value['worktree'] is! String) {
    _invalid('worktree');
  }
  final steps = value['steps'];
  if (steps is! List || steps.length != 2) _invalid('steps');
  final ids = <String>{};
  for (final step in steps) {
    if (step is! Map ||
        step['step_id'] is! String ||
        !ids.add(step['step_id'] as String) ||
        step['participant'] is! String ||
        step['mode'] is! String ||
        step['state'] is! String ||
        (step['job_id'] != null && step['job_id'] is! String) ||
        !_strings(step['progress'] ?? const []) ||
        !_strings(step['changed_files'] ?? const []) ||
        (step['result'] != null && step['result'] is! String) ||
        (step['error'] != null && step['error'] is! String)) {
      _invalid('step');
    }
  }
  final transfers = value['transfers'];
  if (transfers is! List || transfers.length != 1) _invalid('transfers');
  for (final transfer in transfers) {
    if (transfer is! Map ||
        transfer['transfer_id'] is! String ||
        !ids.contains(transfer['from']) ||
        !ids.contains(transfer['to']) ||
        transfer['from'] == transfer['to'] ||
        transfer['state'] is! String) {
      _invalid('transfer');
    }
    if (transfer['artifact'] != null) _checkArtifact(transfer['artifact']);
  }
  return freezeJson(Map<String, dynamic>.from(value));
}

/// Validates the full output waiting to be handed over.
Json parseRunArtifact(Object? value) {
  _checkArtifact(value, text: true);
  return freezeJson(Map<String, dynamic>.from(value as Map));
}
