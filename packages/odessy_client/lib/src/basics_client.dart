/// Versioned agent-basics transport. All requests reuse the owning LocalApi.
library;

import 'agent_basics.dart';
import 'models.dart';

typedef BasicsRequest = Future<Json> Function(
    String path, String method, Json? body);

abstract interface class AgentBasicsConnection {
  AgentBasicsClient get agentBasics;
}

Never _invalid(String field) =>
    throw FormatException('Invalid agent-basics $field');
Json _map(Object? v) => v is Map<String, dynamic> ? v : _invalid('object');
String _text(Json v, String k, {int max = 4096, bool empty = false}) {
  final x = v[k];
  if (x is! String || (!empty && x.isEmpty) || x.length > max) _invalid(k);
  return x;
}

int _integer(Json v, String k, {int min = 0}) {
  final x = v[k];
  if (x is! int || x < min) _invalid(k);
  return x;
}

bool _boolean(Json v, String k) => v[k] is bool ? v[k] as bool : _invalid(k);
void _shape(Json v, Set<String> keys, {bool version = true}) {
  if (v.keys.toSet().difference(keys).isNotEmpty ||
      !keys.every(v.containsKey) ||
      (version && v['version'] != 1)) _invalid('schema');
}

List<T> _list<T>(Json v, String k, T Function(Json) parse, {int max = 1000}) {
  final x = v[k];
  if (x is! List || x.length > max) _invalid(k);
  return List.unmodifiable(x.map((e) => parse(_map(e))));
}

String _id(Json v, String k) {
  final x = _text(v, k, max: 32);
  if (!RegExp(r'^[a-f0-9]{32}$').hasMatch(x)) _invalid(k);
  return x;
}

String _checkpointId(Json v, String k) {
  final x = _text(v, k, max: 37);
  if (!RegExp(r'^(ckpt_)?[a-f0-9]{32}$').hasMatch(x)) _invalid(k);
  return x;
}

String _date(Json v, String k) {
  final x = _text(v, k, max: 64);
  if (DateTime.tryParse(x) == null || !x.endsWith('Z') && !x.endsWith('+00:00'))
    _invalid(k);
  return x;
}

class BasicsCapabilities {
  BasicsCapabilities.fromJson(Json value) : data = freezeJson(value) {
    _shape(data, {
      'version',
      'service_instance_id',
      'conversations',
      'custom_continuation',
      'harness_continuation',
      'access_modes',
      'context',
      'memory',
      'retrieval',
      'terminal',
      'checkpoints'
    });
    _text(data, 'service_instance_id', max: 256);
    for (final key in data.keys
        .where((k) => k != 'version' && k != 'service_instance_id')) {
      _boolean(data, key);
    }
  }
  final Json data;
  String get serviceInstance => data['service_instance_id'] as String;
  bool supports(String key) => data[key] == true;
}

class SavedProject {
  SavedProject.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {'version', 'id', 'label', 'created_at'});
    _id(data, 'id');
    _text(data, 'label', max: 120);
    _date(data, 'created_at');
  }
  final Json data;
  String get id => data['id'] as String;
  String get label => data['label'] as String;
}

class SavedConversation {
  SavedConversation.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'id',
      'project_id',
      'engine',
      'profile',
      'title',
      'pinned',
      'archived',
      'revision',
      'created_at',
      'updated_at',
      'continuation'
    });
    _id(data, 'id');
    _id(data, 'project_id');
    _text(data, 'profile', max: 200);
    _text(data, 'title', max: 120);
    _integer(data, 'revision');
    _boolean(data, 'pinned');
    _boolean(data, 'archived');
    _date(data, 'created_at');
    _date(data, 'updated_at');
    if (!['legacy', 'harness'].contains(data['engine']) ||
        !['continuable', 'history_only'].contains(data['continuation']) ||
        data['engine'] == 'harness' && data['continuation'] != 'history_only')
      _invalid('continuation');
  }
  final Json data;
  String get id => data['id'] as String;
  String get projectId => data['project_id'] as String;
  String get title => data['title'] as String;
  String get engine => data['engine'] as String;
  String get profile => data['profile'] as String;
  int get revision => data['revision'] as int;
  bool get pinned => data['pinned'] as bool;
  bool get archived => data['archived'] as bool;
  bool get continuable => data['continuation'] == 'continuable';
}

class SavedEvent {
  SavedEvent.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {'sequence', 'event_id', 'kind', 'created_at', 'payload'},
        version: false);
    _integer(data, 'sequence', min: 1);
    _text(data, 'event_id', max: 256);
    _date(data, 'created_at');
    if (![
      'user',
      'assistant_delta',
      'assistant',
      'tool_call',
      'tool_result',
      'activity',
      'attachment',
      'interrupted'
    ].contains(data['kind'])) _invalid('event kind');
    _map(data['payload']);
  }
  final Json data;
  int get sequence => data['sequence'] as int;
  String get kind => data['kind'] as String;
  Json get payload => data['payload'] as Json;
}

class ConversationPageData {
  ConversationPageData.fromJson(Json v) {
    _shape(v, {'version', 'conversations', 'next_cursor'});
    rows = _list(v, 'conversations', SavedConversation.fromJson, max: 100);
    cursor = _cursor(v);
  }
  late final List<SavedConversation> rows;
  late final String? cursor;
}

String? _cursor(Json v) =>
    v['next_cursor'] == null ? null : _text(v, 'next_cursor', max: 4096);

class MessagePageData {
  MessagePageData.fromJson(Json v) {
    _shape(v, {'version', 'conversation_id', 'messages', 'next_before'});
    conversationId = _id(v, 'conversation_id');
    events = _list(v, 'messages', SavedEvent.fromJson, max: 100);
    before =
        v['next_before'] == null ? null : _integer(v, 'next_before', min: 1);
    for (var i = 1; i < events.length; i++) {
      if (events[i].sequence <= events[i - 1].sequence) _invalid('event order');
    }
  }
  late final String conversationId;
  late final List<SavedEvent> events;
  late final int? before;
}

class SearchMatch {
  SearchMatch.fromJson(Json v) {
    _shape(v, {'conversation_id', 'sequence', 'snippet'}, version: false);
    conversationId = _id(v, 'conversation_id');
    sequence = _integer(v, 'sequence', min: 1);
    snippet = _text(v, 'snippet', max: 400, empty: true);
  }
  late final String conversationId, snippet;
  late final int sequence;
}

class SearchPageData {
  SearchPageData.fromJson(Json v) {
    _shape(v, {'version', 'matches', 'next_cursor'});
    matches = _list(v, 'matches', SearchMatch.fromJson, max: 100);
    cursor = _cursor(v);
  }
  late final List<SearchMatch> matches;
  late final String? cursor;
}

class ResumeReceipt {
  ResumeReceipt.fromJson(Json v) {
    _shape(v, {
      'version',
      'conversation_id',
      'session_id',
      'service_instance_id',
      'continuation'
    });
    conversationId = _id(v, 'conversation_id');
    sessionId = _text(v, 'session_id', max: 256);
    serviceInstance = _text(v, 'service_instance_id', max: 256);
    if (v['continuation'] != 'continuable') _invalid('resume');
  }
  late final String conversationId, sessionId, serviceInstance;
}

class ModeSnapshot {
  ModeSnapshot.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'session_id',
      'service_instance_id',
      'mode',
      'revision',
      'supported_modes',
      'reviewer',
      'scope',
      'effective'
    });
    _text(data, 'session_id', max: 256);
    _text(data, 'service_instance_id', max: 256);
    _integer(data, 'revision');
    _boolean(data, 'effective');
    _text(data, 'reviewer', max: 128);
    final modes = data['supported_modes'];
    if (modes is! List ||
        modes.length > 3 ||
        modes.toSet().length != modes.length ||
        modes.any((e) => !AccessMode.values.any((m) => m.name == e)) ||
        !AccessMode.values.any((m) => m.name == data['mode'])) _invalid('mode');
    if (data['scope'] != null) {
      final scope = _map(data['scope']);
      _shape(scope, {'workspace_id', 'target_id'}, version: false);
      for (final k in scope.keys) {
        if (scope[k] != null) _text(scope, k, max: 4096);
      }
    }
  }
  final Json data;
  String get sessionId => data['session_id'] as String;
  String get serviceInstance => data['service_instance_id'] as String;
  AccessMode get mode => AccessMode.values.byName(data['mode'] as String);
  int get revision => data['revision'] as int;
  bool get effective => data['effective'] as bool;
  List<AccessMode> get supported =>
      List.unmodifiable((data['supported_modes'] as List)
          .map((e) => AccessMode.values.byName(e as String)));
  String get scopeLabel {
    final scope = data['scope'] as Json?;
    return scope == null
        ? 'No granted scope'
        : 'Workspace: ${scope['workspace_id'] ?? 'none'} · Target: ${scope['target_id'] ?? 'local'}';
  }
}

/// Strict immutable advanced documents; no payload is treated as authority.
class MemoryFact {
  MemoryFact.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'id',
      'project_id',
      'text',
      'enabled',
      'revision',
      'created_at',
      'updated_at',
      'source'
    });
    _id(data, 'id');
    _id(data, 'project_id');
    _text(data, 'text', max: 2000);
    _integer(data, 'revision');
    _boolean(data, 'enabled');
    _date(data, 'created_at');
    _date(data, 'updated_at');
    if (data['source'] != 'user') _invalid('memory source');
  }
  final Json data;
  String get id => data['id'] as String;
  String get text => data['text'] as String;
  int get revision => data['revision'] as int;
  bool get enabled => data['enabled'] as bool;
}

class IndexedDocument {
  IndexedDocument.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'id',
      'project_id',
      'path',
      'revision',
      'source_hash',
      'status',
      'chunks',
      'error'
    });
    _id(data, 'id');
    _id(data, 'project_id');
    _text(data, 'path');
    _integer(data, 'revision');
    _text(data, 'source_hash', max: 64);
    _integer(data, 'chunks');
    if (!['ready', 'stale', 'error'].contains(data['status']))
      _invalid('document status');
    if (data['error'] != null) _text(data, 'error', max: 4096, empty: true);
  }
  final Json data;
  String get id => data['id'] as String;
  String get path => data['path'] as String;
  int get revision => data['revision'] as int;
}

class Citation {
  Citation.fromJson(Json v) : data = freezeJson(v) {
    _shape(
        data,
        {
          'document_id',
          'path',
          'start_line',
          'end_line',
          'source_hash',
          'text',
          'score'
        },
        version: false);
    _id(data, 'document_id');
    _text(data, 'path');
    _integer(data, 'start_line', min: 1);
    if (_integer(data, 'end_line', min: 1) < data['start_line'])
      _invalid('citation range');
    _text(data, 'source_hash', max: 64);
    _text(data, 'text', max: 24000, empty: true);
    if (data['score'] is! num || !(data['score'] as num).isFinite)
      _invalid('score');
  }
  final Json data;
}

class UsedContext {
  UsedContext.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'conversation_id',
      'revision',
      'summary',
      'memory_ids',
      'citations',
      'chars_used',
      'budget_chars',
      'method'
    });
    _id(data, 'conversation_id');
    _integer(data, 'revision');
    _text(data, 'method', max: 256);
    if (_integer(data, 'chars_used') > _integer(data, 'budget_chars'))
      _invalid('context budget');
    final ids = data['memory_ids'];
    if (ids is! List || ids.length > 100) _invalid('memory ids');
    for (final id in ids) {
      _id({'id': id}, 'id');
    }
    _list(data, 'citations', Citation.fromJson, max: 100);
    if (data['summary'] != null) {
      final s = _map(data['summary']);
      _shape(
          s,
          {
            'id',
            'conversation_id',
            'revision',
            'through_sequence',
            'source_hash',
            'text',
            'method'
          },
          version: false);
      _id(s, 'id');
      _id(s, 'conversation_id');
      _integer(s, 'revision');
      _integer(s, 'through_sequence');
      _text(s, 'source_hash', max: 64);
      _text(s, 'text', max: 24000, empty: true);
      _text(s, 'method', max: 256);
    }
  }
  final Json data;
}

class TerminalHandle {
  TerminalHandle.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'id',
      'session_id',
      'service_instance_id',
      'revision',
      'status',
      'exit_code'
    });
    _id(data, 'id');
    _text(data, 'session_id', max: 256);
    _text(data, 'service_instance_id', max: 256);
    _integer(data, 'revision');
    if (!['running', 'exited', 'unknown'].contains(data['status']) ||
        data['exit_code'] != null && data['exit_code'] is! int)
      _invalid('terminal');
  }
  final Json data;
  String get id => data['id'] as String;
  String get sessionId => data['session_id'] as String;
  String get serviceInstance => data['service_instance_id'] as String;
  int get revision => data['revision'] as int;
  bool get running => data['status'] == 'running';
}

class TerminalOutput {
  TerminalOutput.fromJson(Json v) {
    _shape(v, {'version', 'handle', 'output', 'next_offset', 'truncated'});
    handle = TerminalHandle.fromJson(_map(v['handle']));
    output = _text(v, 'output', max: 65536, empty: true);
    nextOffset = _integer(v, 'next_offset');
    truncated = _boolean(v, 'truncated');
  }
  late final TerminalHandle handle;
  late final String output;
  late final int nextOffset;
  late final bool truncated;
}

class FileCheckpoint {
  FileCheckpoint.fromJson(Json v) : data = freezeJson(v) {
    final required = {
      'version',
      'id',
      'label',
      'created_at',
      'revision',
      'workspace_id'
    };
    if (data['version'] != 1 ||
        !required.every(data.containsKey) ||
        data.keys.any((k) => !{...required, 'files', 'bytes'}.contains(k)))
      _invalid('checkpoint schema');
    for (final k in ['files', 'bytes']) {
      if (data.containsKey(k)) _integer(data, k);
    }
    _checkpointId(data, 'id');
    _text(data, 'label', max: 120);
    _date(data, 'created_at');
    _integer(data, 'revision');
    _text(data, 'workspace_id');
  }
  final Json data;
  String get id => data['id'] as String;
  String get label => data['label'] as String;
}

class CheckpointPreview {
  CheckpointPreview.fromJson(Json v) : data = freezeJson(v) {
    _shape(data, {
      'version',
      'checkpoint_id',
      'expected_revision',
      'preview_hash',
      'changes',
      'unsupported'
    });
    _checkpointId(data, 'checkpoint_id');
    _integer(data, 'expected_revision');
    if (!RegExp(r'^[a-f0-9]{64}$')
        .hasMatch(_text(data, 'preview_hash', max: 64)))
      _invalid('preview hash');
    for (final k in ['changes', 'unsupported']) {
      if (data[k] is! List || (data[k] as List).length > 1000) _invalid(k);
    }
  }
  final Json data;
}

class AgentBasicsClient {
  AgentBasicsClient(this._request);
  final BasicsRequest _request;
  String _p(String id) => Uri.encodeComponent(id);
  String _conversation(String id) => '/v1/conversations/${_p(id)}';
  String _project(String id) => '/v1/projects/${_p(id)}';
  String _session(String id) => '/v1/sessions/${_p(id)}';
  String _query(String path, Map<String, String> q) =>
      Uri(path: path, queryParameters: q).toString();
  Future<Json> _get(String path) => _request(path, 'GET', null);
  Future<BasicsCapabilities> capabilities() async =>
      BasicsCapabilities.fromJson(await _get('/v1/agent-basics/capabilities'));
  Future<List<SavedProject>> projects() async {
    final v = await _get('/v1/projects');
    _shape(v, {'version', 'projects'});
    return _list(v, 'projects', SavedProject.fromJson);
  }

  Future<SavedProject> createProject(String label) async =>
      SavedProject.fromJson(
          await _request('/v1/projects', 'POST', {'label': label}));
  Future<ConversationPageData> conversations(
          {String? projectId, bool archived = false, String? cursor}) async =>
      ConversationPageData.fromJson(await _get(_query('/v1/conversations', {
        'archived': '$archived',
        'limit': '50',
        if (projectId != null) 'project_id': projectId,
        if (cursor != null) 'cursor': cursor
      })));
  Future<SavedConversation> createConversation(String projectId,
          {String title = 'New chat'}) async =>
      SavedConversation.fromJson(await _request('/v1/conversations', 'POST',
          {'project_id': projectId, 'title': title}));
  Future<SavedConversation> conversation(String id) async =>
      SavedConversation.fromJson(await _get(_conversation(id)));
  Future<MessagePageData> messages(String id, {int? before}) async =>
      MessagePageData.fromJson(await _get(_query(
          '${_conversation(id)}/messages',
          {'limit': '100', if (before != null) 'before': '$before'})));
  Future<SearchPageData> search(String query,
          {String? projectId, String? cursor}) async =>
      SearchPageData.fromJson(await _get(_query('/v1/conversations/search', {
        'query': query,
        'limit': '50',
        if (projectId != null) 'project_id': projectId,
        if (cursor != null) 'cursor': cursor
      })));
  Future<SavedConversation> update(SavedConversation row,
          {String? title, bool? pinned, bool? archived}) async =>
      SavedConversation.fromJson(
          await _request(_conversation(row.id), 'PATCH', {
        'expected_revision': row.revision,
        if (title != null) 'title': title,
        if (pinned != null) 'pinned': pinned,
        if (archived != null) 'archived': archived
      }));
  Future<void> delete(SavedConversation row) async {
    await _request(
        _conversation(row.id), 'DELETE', {'expected_revision': row.revision});
  }

  Future<Json> export(String id) async {
    final v = await _get('${_conversation(id)}/export');
    _shape(v, {'version', 'conversation', 'events'});
    final row = SavedConversation.fromJson(_map(v['conversation']));
    if (row.id != id) _invalid('export identity');
    _list(v, 'events', SavedEvent.fromJson, max: 100000);
    return freezeJson(v);
  }

  Future<ResumeReceipt> resume(SavedConversation row) async =>
      ResumeReceipt.fromJson(await _request('${_conversation(row.id)}/resume',
          'POST', {'expected_revision': row.revision}));
  Future<SavedConversation> bind(
          SavedConversation row, String sid, String instance) async =>
      SavedConversation.fromJson(
          await _request('${_conversation(row.id)}/bind-workspace', 'POST', {
        'expected_revision': row.revision,
        'session_id': sid,
        'service_instance_id': instance
      }));
  Future<ModeSnapshot> mode(String sid) async =>
      ModeSnapshot.fromJson(await _get('${_session(sid)}/access-mode'));
  Future<ModeSnapshot> setMode(ModeSnapshot current, AccessMode mode) async =>
      ModeSnapshot.fromJson(
          await _request('${_session(current.sessionId)}/access-mode', 'PUT', {
        'mode': mode.name,
        'expected_revision': current.revision,
        'service_instance_id': current.serviceInstance
      }));
  Future<UsedContext> usedContext(String cid) async =>
      UsedContext.fromJson(await _get('${_conversation(cid)}/context'));
  Future<List<MemoryFact>> memory(String pid) async {
    final v = await _get('${_project(pid)}/memory');
    _shape(v, {'version', 'items'});
    return _list(v, 'items', MemoryFact.fromJson);
  }

  Future<MemoryFact> createMemory(String pid, String text) async =>
      MemoryFact.fromJson(
          await _request('${_project(pid)}/memory', 'POST', {'text': text}));
  Future<MemoryFact> updateMemory(String pid, MemoryFact fact,
          {String? text, bool? enabled}) async =>
      MemoryFact.fromJson(
          await _request('${_project(pid)}/memory/${_p(fact.id)}', 'PATCH', {
        'expected_revision': fact.revision,
        if (text != null) 'text': text,
        if (enabled != null) 'enabled': enabled
      }));
  Future<void> deleteMemory(String pid, MemoryFact fact) async {
    await _request('${_project(pid)}/memory/${_p(fact.id)}', 'DELETE',
        {'expected_revision': fact.revision});
  }

  Future<List<IndexedDocument>> documents(String pid) async {
    final v = await _get('${_project(pid)}/documents');
    _shape(v, {'version', 'documents'});
    return _list(v, 'documents', IndexedDocument.fromJson, max: 100);
  }

  Future<IndexedDocument> index(
          String pid, String sid, String instance, String path) async =>
      IndexedDocument.fromJson(await _request(
          '${_project(pid)}/documents',
          'POST',
          {'session_id': sid, 'service_instance_id': instance, 'path': path}));
  Future<void> removeDocument(String pid, IndexedDocument d) async {
    await _request('${_project(pid)}/documents/${_p(d.id)}', 'DELETE',
        {'expected_revision': d.revision});
  }

  Future<Json> retrieve(String pid, String sid, String query) async {
    final v = await _get(_query('${_project(pid)}/retrieve',
        {'session_id': sid, 'query': query, 'limit': '5'}));
    _shape(v, {'version', 'citations', 'stale_document_ids'});
    _list(v, 'citations', Citation.fromJson, max: 10);
    if (v['stale_document_ids'] is! List) _invalid('stale documents');
    for (final id in v['stale_document_ids'] as List) {
      _id({'id': id}, 'id');
    }
    return freezeJson(v);
  }

  Future<TerminalHandle> createTerminal(String sid,
          {int columns = 80, int rows = 24}) async =>
      TerminalHandle.fromJson(await _request('${_session(sid)}/terminals',
          'POST', {'columns': columns, 'rows': rows}));
  String _terminal(TerminalHandle h) =>
      '${_session(h.sessionId)}/terminals/${_p(h.id)}';
  Future<TerminalOutput> output(TerminalHandle h, int after) async =>
      TerminalOutput.fromJson(
          await _get('${_terminal(h)}/output?after=$after'));
  Future<void> input(TerminalHandle h, String text) async {
    await _request('${_terminal(h)}/input', 'POST',
        {'text': text, 'expected_revision': h.revision});
  }

  Future<TerminalHandle> resize(
          TerminalHandle h, int columns, int rows) async =>
      TerminalHandle.fromJson(await _request('${_terminal(h)}/size', 'PUT',
          {'columns': columns, 'rows': rows, 'expected_revision': h.revision}));
  Future<TerminalHandle> interrupt(TerminalHandle h) async =>
      TerminalHandle.fromJson(await _request('${_terminal(h)}/interrupt',
          'POST', {'expected_revision': h.revision}));
  Future<TerminalHandle> closeTerminal(TerminalHandle h) async =>
      TerminalHandle.fromJson(await _request(
          _terminal(h), 'DELETE', {'expected_revision': h.revision}));
  Future<List<FileCheckpoint>> checkpoints(String sid) async {
    final v = await _get('${_session(sid)}/checkpoints');
    _shape(v, {'version', 'checkpoints'});
    return _list(v, 'checkpoints', FileCheckpoint.fromJson, max: 100);
  }

  Future<FileCheckpoint> createCheckpoint(String sid, String label) async =>
      FileCheckpoint.fromJson(await _request(
          '${_session(sid)}/checkpoints', 'POST', {'label': label}));
  Future<CheckpointPreview> preview(String sid, String id) async =>
      CheckpointPreview.fromJson(
          await _get('${_session(sid)}/checkpoints/${_p(id)}/preview'));
  Future<Json> restore(String sid, CheckpointPreview preview) async {
    final v = await _request(
        '${_session(sid)}/checkpoints/${_p(preview.data['checkpoint_id'] as String)}/restore',
        'POST', {
      'expected_revision': preview.data['expected_revision'],
      'preview_hash': preview.data['preview_hash']
    });
    _shape(v, {'version', 'checkpoint_id', 'safety_checkpoint_id', 'status'});
    _checkpointId(v, 'checkpoint_id');
    _checkpointId(v, 'safety_checkpoint_id');
    if (v['checkpoint_id'] != preview.data['checkpoint_id'] ||
        v['status'] != 'restored') _invalid('restore receipt');
    return freezeJson(v);
  }
}
