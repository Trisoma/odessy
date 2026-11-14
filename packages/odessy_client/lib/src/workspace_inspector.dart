import 'dart:convert';
import 'models.dart';

/// Optional read-only capability. The caller supplies LocalApi's existing,
/// authenticated bounded JSON transport, never a second HTTP/token owner.
abstract interface class WorkspaceInspectorApi {
  Future<WorkspaceDescriptor> workspace(String sid);
  Future<WorkspaceTree> listWorkspace(String sid, {String path = '.'});
  Future<WorkspaceTextFile> readWorkspaceFile(String sid, String path);
}

class WorkspaceInspectorClient implements WorkspaceInspectorApi {
  WorkspaceInspectorClient(this.readJson);
  final Future<Json> Function(String path) readJson;
  String _route(String sid, String suffix, [String? path]) {
    workspaceIdentity(sid);
    return '/v1/sessions/${Uri.encodeComponent(sid)}/workspace$suffix'
        '${path == null ? '' : '?path=${Uri.encodeQueryComponent(workspacePath(path))}'}';
  }

  @override
  Future<WorkspaceDescriptor> workspace(String sid) async {
    final value = WorkspaceDescriptor.fromJson(await readJson(_route(sid, '')));
    if (value.sessionId != sid) throw const FormatException('Session changed');
    return value;
  }

  @override
  Future<WorkspaceTree> listWorkspace(String sid, {String path = '.'}) async {
    final value =
        WorkspaceTree.fromJson(await readJson(_route(sid, '/tree', path)));
    if (value.sessionId != sid || value.path != workspacePath(path)) {
      throw const FormatException('Workspace response changed');
    }
    return value;
  }

  @override
  Future<WorkspaceTextFile> readWorkspaceFile(String sid, String path) async {
    final value =
        WorkspaceTextFile.fromJson(await readJson(_route(sid, '/file', path)));
    if (value.sessionId != sid || value.path != workspacePath(path)) {
      throw const FormatException('Workspace response changed');
    }
    return value;
  }
}

String workspaceIdentity(Object? value) {
  if (value is! String ||
      value.isEmpty ||
      value.length > 256 ||
      value.runes.any((c) => c < 32)) {
    throw const FormatException('Invalid workspace identity');
  }
  return value;
}

String workspacePath(Object? value) {
  if (value is! String ||
      value.isEmpty ||
      value.length > 2048 ||
      value.startsWith('/') ||
      value.startsWith('~') ||
      value.contains(':') ||
      value.runes.any((c) => c < 32)) {
    throw const FormatException('Invalid relative workspace path');
  }
  final parts = value.replaceAll('\\', '/').split('/');
  if (parts.contains('..') || parts.first.isEmpty) {
    throw const FormatException('Invalid relative workspace path');
  }
  final normalized = parts.where((p) => p.isNotEmpty && p != '.').join('/');
  return normalized.isEmpty ? '.' : normalized;
}

String _hash(Object? value) {
  if (value is! String || !RegExp(r'^[a-f0-9]{64}$').hasMatch(value)) {
    throw const FormatException('Invalid content hash');
  }
  return value;
}

class WorkspaceContext {
  WorkspaceContext.fromJson(Json value)
      : serviceInstanceId = workspaceIdentity(value['service_instance_id']),
        sessionId = workspaceIdentity(value['session_id']),
        workspaceId = workspaceIdentity(value['workspace_id']);
  final String serviceInstanceId, sessionId, workspaceId;
  bool sameContext(WorkspaceContext other) =>
      serviceInstanceId == other.serviceInstanceId &&
      sessionId == other.sessionId &&
      workspaceId == other.workspaceId;
}

class WorkspaceDescriptor extends WorkspaceContext {
  WorkspaceDescriptor.fromJson(Json value) : super.fromJson(value) {
    label = workspaceIdentity(value['label']);
    final caps = value['capabilities'];
    if (caps is! Json ||
        ['read_files', 'proposed_changes', 'recorded_output']
            .any((key) => caps[key] is! bool)) {
      throw const FormatException('Invalid workspace capabilities');
    }
    readFiles = caps['read_files'] as bool;
    proposedChanges = caps['proposed_changes'] as bool;
    recordedOutput = caps['recorded_output'] as bool;
  }
  late final String label;
  late final bool readFiles, proposedChanges, recordedOutput;
}

class WorkspaceEntry {
  WorkspaceEntry.fromJson(Json value, String parent)
      : name = workspaceIdentity(value['name']),
        path = workspacePath(value['path']),
        kind = value['kind'] is String ? value['kind'] as String : '' {
    if (!['file', 'directory'].contains(kind) ||
        name.contains(RegExp(r'[/\\:]')) ||
        name == '.' ||
        name == '..' ||
        path != (parent == '.' ? name : '$parent/$name')) {
      throw const FormatException('Invalid directory entry');
    }
  }
  final String name, path, kind;
}

class WorkspaceTree extends WorkspaceContext {
  WorkspaceTree.fromJson(Json value) : super.fromJson(value) {
    path = workspacePath(value['path']);
    final rows = value['entries'];
    if (rows is! List ||
        rows.length > 200 ||
        rows.any((r) => r is! Json) ||
        value['truncated'] is! bool)
      throw const FormatException('Invalid directory');
    entries = List.unmodifiable(
        rows.map((r) => WorkspaceEntry.fromJson(r as Json, path)));
    if (entries.map((e) => e.path).toSet().length != entries.length) {
      throw const FormatException('Duplicate workspace entry');
    }
    truncated = value['truncated'] as bool;
  }
  late final String path;
  late final List<WorkspaceEntry> entries;
  late final bool truncated;
}

class WorkspaceTextFile extends WorkspaceContext {
  WorkspaceTextFile.fromJson(Json value) : super.fromJson(value) {
    path = workspacePath(value['path']);
    sha256 = _hash(value['sha256']);
    final content = value['text'];
    if (content is! String ||
        utf8.encode(content).length > 256 * 1024 ||
        content.runes.any((c) => c < 32 && ![9, 10, 13].contains(c)) ||
        value['truncated'] != false) {
      throw const FormatException('Invalid bounded text file');
    }
    text = content;
  }
  late final String path, text, sha256;
}

class ProposedChange {
  ProposedChange.fromJson(Json value)
      : sessionId = workspaceIdentity(value['session_id']),
        requestId = workspaceIdentity(value['request_id']),
        path = workspacePath(value['path']),
        beforeSha256 = _hash(value['before_sha256']),
        afterSha256 = _hash(value['after_sha256']),
        beforeText = _bounded(value['before_text'], 256 * 1024),
        afterText = _bounded(value['after_text'], 256 * 1024);
  final String sessionId,
      requestId,
      path,
      beforeSha256,
      afterSha256,
      beforeText,
      afterText;
}

String _bounded(Object? value, int max) {
  if (value is! String || utf8.encode(value).length > max) {
    throw const FormatException('Invalid bounded output');
  }
  return value;
}

class RecordedCommandOutput {
  RecordedCommandOutput.fromJson(Json value)
      : sessionId = workspaceIdentity(value['session_id']),
        turnId = workspaceIdentity(value['turn_id']),
        callId = workspaceIdentity(value['call_id']),
        toolName = workspaceIdentity(value['tool_name']),
        state = workspaceIdentity(value['state']),
        stdout = _bounded(value['stdout'], 64 * 1024),
        stderr = _bounded(value['stderr'], 64 * 1024),
        exitCode = _exitCode(value['exit_code']),
        truncated = _boolean(value['truncated']) {
    if (!['terminal.run', 'laptop.terminal.run'].contains(toolName) ||
        ![
          'completed',
          'error',
          'denied',
          'cancelled',
          'timeout',
          'skipped',
          'unavailable'
        ].contains(state)) {
      throw const FormatException('Invalid command result');
    }
  }
  final String sessionId, turnId, callId, toolName, state, stdout, stderr;
  final int? exitCode;
  final bool truncated;
}

/// Explicit reviewed text context. Send admission must call revalidateAttachments
/// immediately before its existing once-only send; never auto-send on selection.
int? _exitCode(Object? value) {
  if (value != null && value is! int)
    throw const FormatException('Invalid exit code');
  return value as int?;
}

bool _boolean(Object? value) {
  if (value is! bool) throw const FormatException('Invalid result bound');
  return value;
}

class WorkspaceAttachment {
  WorkspaceAttachment(this.file);
  final WorkspaceTextFile file;
}

Future<String> revalidateAttachments(WorkspaceInspectorApi api,
    WorkspaceDescriptor context, List<WorkspaceAttachment> attachments) async {
  final parts = <String>[];
  var bytes = 0;
  for (final attachment in attachments) {
    final file = attachment.file;
    if (!context.sameContext(file))
      throw StateError('Attachment context changed');
    final fresh = await api.readWorkspaceFile(context.sessionId, file.path);
    if (!context.sameContext(fresh) ||
        fresh.sha256 != file.sha256 ||
        fresh.text != file.text) {
      throw StateError('Attachment changed; reread and review before sending');
    }
    final part =
        '--- Workspace text: ${file.path} (sha256:${file.sha256}) ---\n'
        '${file.text}\n--- End workspace text ---';
    bytes += utf8.encode(part).length;
    if (bytes > 32 * 1024)
      throw StateError('Combined attachment context exceeds 32 KiB');
    parts.add(part);
  }
  return parts.join('\n\n');
}

/// Existing session result events already contain sanitized Custom observations.
/// Harness progress-only events deliberately produce no invented output.
List<RecordedCommandOutput> recordedSessionOutputs(Session snapshot) {
  final projected = snapshot.events
      .where((e) => e.type == 'command_output')
      .toList()
    ..sort((a, b) => a.id.compareTo(b.id));
  if (projected.isNotEmpty) {
    final rows = <RecordedCommandOutput>[];
    final seen = <String>{};
    for (final event in projected) {
      try {
        final output = RecordedCommandOutput.fromJson(event.data);
        if (output.sessionId != snapshot.id) continue;
        if (seen.add('${output.turnId}/${output.callId}')) rows.add(output);
      } on FormatException {
        // Malformed public projection is unavailable, never fabricated stdout.
      }
    }
    return List.unmodifiable(
        rows.skip(rows.length > 64 ? rows.length - 64 : 0));
  }
  final events = snapshot.events.where((e) => e.type == 'result').toList()
    ..sort((a, b) => a.id.compareTo(b.id));
  final seen = <String>{};
  final output = <RecordedCommandOutput>[];
  for (final event in events) {
    final observations = event.data['tool_observations'];
    if (observations is! List) continue;
    for (var index = 0; index < observations.length && index < 64; index++) {
      final observation = observations[index];
      if (observation is! Map) continue;
      final request = observation['request'], result = observation['result'];
      if (request is! Map ||
          result is! Map ||
          !['terminal.run', 'laptop.terminal.run'].contains(request['name']))
        continue;
      final stdout = result['stdout'] ?? '', stderr = result['stderr'] ?? '';
      if (stdout is! String || stderr is! String) continue;
      final turn =
          'result:${event.id}'; // Honest result-event identity, not an invented runtime turn UUID.
      final call = observation['call_id'] is String
          ? observation['call_id'] as String
          : '$turn:observation:$index';
      if (!seen.add('$turn/$call')) continue;
      final state = switch (result['status']) {
        'ok' => 'completed',
        'failed' => 'error',
        'completed' ||
        'error' ||
        'denied' ||
        'cancelled' ||
        'timeout' ||
        'skipped' =>
          result['status'] as String,
        _ => 'unavailable',
      };
      var truncated = result['truncated'] == true ||
          result['stdout_truncated'] == true ||
          result['stderr_truncated'] == true;
      String bound(String text) {
        final bytes = utf8.encode(text);
        if (bytes.length <= 64 * 1024) return text;
        truncated = true;
        var bounded =
            utf8.decode(bytes.take(64 * 1024).toList(), allowMalformed: true);
        while (utf8.encode(bounded).length > 64 * 1024) {
          bounded = bounded.substring(0, bounded.length - 1);
        }
        return bounded;
      }

      final out = bound(stdout), err = bound(stderr);
      output.add(RecordedCommandOutput.fromJson({
        'session_id': snapshot.id,
        'turn_id': turn,
        'call_id': call,
        'tool_name': request['name'],
        'state': state,
        'stdout': out,
        'stderr': err,
        'exit_code': result['exit_code'] is int ? result['exit_code'] : null,
        'truncated': truncated
      }));
    }
  }
  return List.unmodifiable(
      output.skip(output.length > 64 ? output.length - 64 : 0));
}
