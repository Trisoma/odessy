import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

/// One transport/lifecycle owner for durable browsing and advanced controls.
/// Selection is data only. Only explicit Resume attaches a runtime.
class BasicsController extends ChangeNotifier {
  BasicsController(this.session, {AgentBasicsClient? client})
      : _override = client {
    session.addListener(_observe);
    _observe();
  }
  final DesktopSession session;
  final AgentBasicsClient? _override;
  AgentBasicsClient? _client;
  OdessyApi? _transport;
  String? _sid;
  bool _disposed = false;
  int _epoch = 0;
  bool busy = false;
  String? error, notice;
  BasicsCapabilities? capabilities;
  ModeSnapshot? mode;
  List<SavedProject> projects = const [];
  List<SavedConversation> conversations = const [];
  List<SavedEvent> events = const [];
  List<SearchMatch> matches = const [];
  SavedConversation? selected;
  SavedConversation? liveConversation;
  String? projectId, cursor, searchCursor;
  int? before;
  bool archived = false;
  String query = '';
  UsedContext? contextUsed;
  List<MemoryFact> facts = const [];
  List<IndexedDocument> documents = const [];
  Json? retrieval;
  List<FileCheckpoint> checkpoints = const [];
  CheckpointPreview? preview;
  TerminalHandle? terminal;
  int outputOffset = 0;
  String terminalText = '';
  bool outputTruncated = false;
  final _terminalBySession = <String, TerminalHandle>{};
  final _terminalTextBySession = <String, String>{};
  final _terminalOffsetBySession = <String, int>{};

  bool supports(String feature) => capabilities?.supports(feature) == true;
  bool get live =>
      session.connected && _sid != null && _sid == session.session?.id;
  bool get canAct => !busy && live && capabilities != null;
  String? get effectiveProjectId =>
      selected?.projectId ?? projectId ?? liveConversation?.projectId;
  String? get effectiveConversationId =>
      selected?.id ?? session.session?.conversationId;
  String get modeDescription => mode == null
      ? 'Permissions unavailable'
      : '${mode!.effective ? 'Effective' : 'Not effective'} · ${mode!.scopeLabel}. '
          'Custom tool dispatch only; jobs, handoffs and terminal grants remain separate.';
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  void _observe() {
    if (_disposed) return;
    if (!identical(_transport, session.api)) {
      ++_epoch;
      _transport = session.api;
      _client = _override ??
          (session.api is AgentBasicsConnection
              ? (session.api as AgentBasicsConnection).agentBasics
              : null);
      capabilities = null;
      mode = null;
      selected = null;
      liveConversation = null;
      projects = const [];
      conversations = const [];
      events = const [];
      matches = const [];
      facts = const [];
      documents = const [];
      checkpoints = const [];
      contextUsed = null;
      retrieval = null;
      preview = null;
      projectId = null;
      cursor = null;
      before = null;
      terminal = null;
      terminalText = '';
      outputOffset = 0;
      busy = false;
      _terminalBySession.clear();
      _terminalTextBySession.clear();
      _terminalOffsetBySession.clear();
      _sid = null;
    }
    final sid = session.connected ? session.session?.id : null;
    if (sid != _sid) {
      if (_sid != null && terminal != null) {
        _terminalBySession[_sid!] = terminal!;
        _terminalTextBySession[_sid!] = terminalText;
        _terminalOffsetBySession[_sid!] = outputOffset;
      }
      ++_epoch;
      _sid = sid;
      liveConversation = null;
      mode = null;
      busy = false;
      preview = null;
      checkpoints = const [];
      terminal = sid == null ? null : _terminalBySession[sid];
      terminalText = sid == null ? '' : _terminalTextBySession[sid] ?? '';
      outputOffset = sid == null ? 0 : _terminalOffsetBySession[sid] ?? 0;
      if (sid != null && _client != null) unawaited(refresh());
    }
    _notify();
  }

  String _message(Object e) => e is ApiFailure
      ? switch (e.status) {
          401 ||
          403 =>
            'Access denied. Check the current session and granted workspace.',
          404 =>
            'This item or endpoint is unavailable. Refresh before retrying.',
          409 =>
            'State changed. Refresh and review the current revision before retrying.',
          413 => 'The response or selected content exceeds the service limit.',
          501 => 'This feature is unsupported by the connected runtime.',
          _ =>
            'Service request failed (HTTP ${e.status}). No fallback was used.',
        }
      : e is TimeoutException
          ? 'No service acknowledgment arrived. The outcome may be unknown; refresh before trying another action.'
          : e is FormatException
              ? 'The service returned an invalid contract. Refresh or update the service.'
              : e is StateError
                  ? e.message.toString()
                  : 'Connection unavailable. No action was replayed.';

  Future<void> _run(Future<void> Function(AgentBasicsClient, int) action,
      {String? feature,
      bool requireLive = false,
      bool invalidateModeOnFailure = false}) async {
    if (_disposed || busy || _client == null) return;
    if (feature != null && !supports(feature)) {
      error =
          '${feature[0].toUpperCase()}${feature.substring(1)} is unavailable in the connected service.';
      _notify();
      return;
    }
    if (requireLive && !live) {
      error = 'A current live session is required.';
      _notify();
      return;
    }
    final epoch = _epoch, client = _client!;
    busy = true;
    error = null;
    notice = null;
    _notify();
    try {
      await action(client, epoch);
    } catch (e) {
      if (_valid(epoch)) {
        if (invalidateModeOnFailure) mode = null;
        error = _message(e);
      }
    } finally {
      if (_valid(epoch)) {
        busy = false;
        _notify();
      }
    }
  }

  bool _valid(int epoch) => !_disposed && epoch == _epoch;
  void _check(int epoch) {
    if (!_valid(epoch)) {
      throw StateError('The service or selected session changed.');
    }
  }

  Future<void> _identity(AgentBasicsClient c, int epoch) async {
    final fresh = await c.capabilities();
    _check(epoch);
    if (capabilities == null ||
        fresh.serviceInstance != capabilities!.serviceInstance) {
      mode = null;
      terminal = null;
      preview = null;
      capabilities = null;
      throw StateError(
          'The service restarted. Refresh before issuing another action.');
    }
  }

  Future<void> refresh() => _run((c, e) async {
        final cap = await c.capabilities();
        _check(e);
        if (capabilities != null &&
            cap.serviceInstance != capabilities!.serviceInstance) {
          selected = null;
          events = const [];
          mode = null;
          terminal = null;
          preview = null;
          _terminalBySession.clear();
          _terminalTextBySession.clear();
          _terminalOffsetBySession.clear();
        }
        capabilities = cap;
        if (cap.supports('conversations')) {
          final p = await c.projects();
          final page =
              await c.conversations(projectId: projectId, archived: archived);
          _check(e);
          projects = p;
          conversations = page.rows;
          cursor = page.cursor;
          final cid = session.session?.conversationId;
          if (cid != null) {
            final row = await c.conversation(cid);
            _check(e);
            if (row.id != cid) {
              throw const FormatException('Wrong live conversation');
            }
            liveConversation = row;
          }
        }
        if (cap.supports('access_modes') && live) {
          final m = await c.mode(_sid!);
          _checkMode(m, e);
          mode = m;
        }
        await _identity(c, e);
      });
  void _checkMode(ModeSnapshot m, int e) {
    _check(e);
    if (m.sessionId != _sid ||
        m.serviceInstance != capabilities?.serviceInstance) {
      throw StateError('Permissions belong to a stale service or session.');
    }
  }

  Future<void> setMode(AccessMode value) => _run((c, e) async {
        final current = mode;
        if (current == null || !current.supported.contains(value)) {
          throw StateError('This mode is unsupported by the selected engine.');
        }
        await _identity(c, e);
        final ModeSnapshot ack;
        try {
          ack = await c.setMode(current, value);
        } catch (_) {
          if (_valid(e)) mode = null;
          rethrow;
        }
        _checkMode(ack, e);
        if (ack.mode != value) {
          throw StateError(
              'The service did not acknowledge the requested mode.');
        }
        mode = ack;
        notice = 'Service acknowledged ${value.name}. ${ack.scopeLabel}';
      },
          feature: 'access_modes',
          requireLive: true,
          invalidateModeOnFailure: true);
  Future<void> filter({String? project, bool? showArchived}) async {
    if (busy) return;
    projectId = project;
    if (showArchived != null) archived = showArchived;
    selected = null;
    events = const [];
    matches = const [];
    query = '';
    await refresh();
  }

  Future<void> more() => _run((c, e) async {
        final next = cursor;
        if (next == null) return;
        final page = await c.conversations(
            projectId: projectId, archived: archived, cursor: next);
        _check(e);
        if (page.cursor == next) {
          throw const FormatException('Cursor did not advance');
        }
        conversations = List.unmodifiable({
          ...{for (final r in conversations) r.id: r},
          ...{for (final r in page.rows) r.id: r}
        }.values);
        cursor = page.cursor;
      }, feature: 'conversations');
  Future<void> search(String text, {bool more = false}) => _run((c, e) async {
        if (text.trim().isEmpty) {
          query = '';
          matches = const [];
          searchCursor = null;
          return;
        }
        final page = await c.search(text,
            projectId: projectId, cursor: more ? searchCursor : null);
        _check(e);
        if (more && page.cursor != null && page.cursor == searchCursor) {
          throw const FormatException('Search cursor did not advance');
        }
        query = text;
        matches = List.unmodifiable([if (more) ...matches, ...page.matches]);
        searchCursor = page.cursor;
      }, feature: 'conversations');
  Future<void> open(String id) => _run((c, e) async {
        final row = await c.conversation(id);
        final page = await c.messages(id);
        _check(e);
        if (row.id != id || page.conversationId != id) {
          throw const FormatException('Wrong conversation');
        }
        selected = row;
        events = page.events;
        before = page.before;
        facts = const [];
        documents = const [];
        contextUsed = null;
        retrieval = null;
        preview = null;
      }, feature: 'conversations');
  Future<void> older() => _run((c, e) async {
        final id = selected?.id, boundary = before;
        if (id == null || boundary == null) return;
        final page = await c.messages(id, before: boundary);
        _check(e);
        if (page.conversationId != id ||
            page.events.any((x) => x.sequence >= boundary) ||
            page.before != null && page.before! >= boundary) {
          throw const FormatException('Invalid message pagination');
        }
        events = List.unmodifiable([...page.events, ...events]);
        before = page.before;
      }, feature: 'conversations');
  Future<void> createProject(String label) => _run((c, e) async {
        await _identity(c, e);
        final row = await c.createProject(label);
        _check(e);
        projects = List.unmodifiable([...projects, row]);
        projectId = row.id;
        conversations = const [];
        selected = null;
        events = const [];
      }, feature: 'conversations');
  Future<void> createChat() => _run((c, e) async {
        final pid = projectId;
        if (pid == null) throw StateError('Select a durable project first.');
        await _identity(c, e);
        final row = await c.createConversation(pid);
        _check(e);
        if (row.projectId != pid) throw const FormatException('Wrong project');
        conversations = List.unmodifiable([row, ...conversations]);
        selected = row;
        events = const [];
        before = null;
        notice =
            'Saved chat created. Bind an authorized workspace explicitly before first resume.';
      }, feature: 'conversations');
  Future<void> update({String? title, bool? pinned, bool? archive}) =>
      _run((c, e) async {
        final row = selected;
        if (row == null) return;
        await _identity(c, e);
        final ack = await c.update(row,
            title: title, pinned: pinned, archived: archive);
        _check(e);
        if (ack.id != row.id || ack.revision <= row.revision) {
          throw const FormatException('Invalid update receipt');
        }
        selected = ack;
        conversations = List.unmodifiable([
          for (final x in conversations)
            if (x.id != ack.id) x,
          if (ack.archived == archived) ack
        ]);
      }, feature: 'conversations');
  Future<void> deleteSelected() => _run((c, e) async {
        final row = selected;
        if (row == null) return;
        if (session.session?.conversationId == row.id) {
          throw StateError(
              'Switch to another live conversation before deleting this conversation.');
        }
        await _identity(c, e);
        await c.delete(row);
        _check(e);
        conversations =
            List.unmodifiable(conversations.where((x) => x.id != row.id));
        selected = null;
        events = const [];
        facts = const [];
        documents = const [];
        contextUsed = null;
        retrieval = null;
        notice =
            'Conversation deleted by the service. Workspace documents were not deleted.';
      }, feature: 'conversations');
  Future<Json?> exportSelected() async {
    Json? result;
    await _run((c, e) async {
      final id = selected?.id;
      if (id == null) return;
      await _identity(c, e);
      final v = await c.export(id);
      _check(e);
      result = v;
    }, feature: 'conversations');
    return result;
  }

  Future<void> bindSelected() => _run((c, e) async {
        final row = selected;
        if (row == null) return;
        await _identity(c, e);
        final ack = await c.bind(row, _sid!, capabilities!.serviceInstance);
        _check(e);
        if (ack.id != row.id || ack.revision <= row.revision) {
          throw const FormatException('Invalid binding receipt');
        }
        selected = ack;
        notice =
            'Workspace identity bound. No grant was copied; Resume is a separate action.';
      }, feature: 'conversations', requireLive: true);
  Future<ResumeReceipt?> resumeSelected() async {
    ResumeReceipt? result;
    await _run((c, e) async {
      final row = selected;
      if (row == null || !row.continuable) {
        throw StateError('This conversation is read-only history.');
      }
      await _identity(c, e);
      final ack = await c.resume(row);
      _check(e);
      if (ack.conversationId != row.id ||
          ack.serviceInstance != capabilities!.serviceInstance) {
        throw StateError('Resume returned a stale identity.');
      }
      result = ack;
    }, feature: 'conversations', requireLive: true);
    return result;
  }

  Future<void> loadContext() => _run((c, e) async {
        final cid = effectiveConversationId;
        if (cid == null) throw StateError('Select a saved conversation.');
        final v = await c.usedContext(cid);
        _check(e);
        if (v.data['conversation_id'] != cid) {
          throw const FormatException('Wrong context');
        }
        contextUsed = v;
      }, feature: 'context');
  String _project() =>
      effectiveProjectId ??
      (throw StateError('Select a durable project or conversation.'));
  Future<void> loadMemory() => _run((c, e) async {
        final p = _project();
        final v = await c.memory(p);
        _check(e);
        if (v.any((x) => x.data['project_id'] != p)) {
          throw const FormatException('Wrong project memory');
        }
        facts = v;
      }, feature: 'memory');
  Future<void> saveMemory(String text, {MemoryFact? fact}) =>
      _run((c, e) async {
        final p = _project();
        await _identity(c, e);
        final MemoryFact ack;
        if (fact == null) {
          ack = await c.createMemory(p, text);
        } else {
          if (fact.data['project_id'] != p) {
            throw StateError('The selected memory belongs to another project.');
          }
          ack = await c.updateMemory(p, fact, text: text);
        }
        _check(e);
        if (ack.data['project_id'] != p ||
            fact != null &&
                (ack.id != fact.id || ack.revision <= fact.revision)) {
          throw const FormatException('Invalid memory receipt');
        }
        final v = await c.memory(p);
        _check(e);
        if (v.any((x) => x.data['project_id'] != p)) {
          throw const FormatException('Wrong project memory');
        }
        facts = v;
      }, feature: 'memory');
  Future<void> toggleMemory(MemoryFact f, bool enabled) => _run((c, e) async {
        final p = _project();
        if (f.data['project_id'] != p) {
          throw StateError('The selected memory belongs to another project.');
        }
        await _identity(c, e);
        final ack = await c.updateMemory(p, f, enabled: enabled);
        _check(e);
        if (ack.data['project_id'] != p ||
            ack.id != f.id ||
            ack.revision <= f.revision ||
            ack.enabled != enabled) {
          throw const FormatException('Invalid memory receipt');
        }
        facts =
            List.unmodifiable([for (final x in facts) x.id == f.id ? ack : x]);
      }, feature: 'memory');
  Future<void> deleteMemory(MemoryFact f) => _run((c, e) async {
        if (f.data['project_id'] != _project()) {
          throw StateError('The selected memory belongs to another project.');
        }
        await _identity(c, e);
        await c.deleteMemory(_project(), f);
        _check(e);
        facts = List.unmodifiable(facts.where((x) => x.id != f.id));
      }, feature: 'memory');
  Future<void> loadDocuments() => _run((c, e) async {
        final p = _project();
        final v = await c.documents(p);
        _check(e);
        if (v.any((x) => x.data['project_id'] != p)) {
          throw const FormatException('Wrong documents');
        }
        documents = v;
      }, feature: 'retrieval');
  Future<void> index(String path) => _run((c, e) async {
        final p = _project();
        await _identity(c, e);
        final ack =
            await c.index(p, _sid!, capabilities!.serviceInstance, path);
        _check(e);
        if (ack.data['project_id'] != p) {
          throw const FormatException('Wrong indexed document');
        }
        documents = List.unmodifiable([
          for (final d in documents)
            if (d.id != ack.id) d,
          ack
        ]);
      }, feature: 'retrieval', requireLive: true);
  Future<void> removeDocument(IndexedDocument d) => _run((c, e) async {
        if (d.data['project_id'] != _project()) {
          throw StateError('The selected document belongs to another project.');
        }
        await _identity(c, e);
        await c.removeDocument(_project(), d);
        _check(e);
        documents = List.unmodifiable(documents.where((x) => x.id != d.id));
      }, feature: 'retrieval');
  Future<void> retrieve(String query) => _run((c, e) async {
        await _identity(c, e);
        final v = await c.retrieve(_project(), _sid!, query);
        _check(e);
        retrieval = v;
      }, feature: 'retrieval', requireLive: true);
  void _checkTerminal(TerminalHandle h, int e) {
    _check(e);
    if (h.sessionId != _sid ||
        h.serviceInstance != capabilities?.serviceInstance) {
      throw StateError('Terminal belongs to a stale service or session.');
    }
  }

  Future<void> createTerminal() => _run((c, e) async {
        if (terminal?.running == true) {
          throw StateError('Close the existing terminal first.');
        }
        await _identity(c, e);
        final h = await c.createTerminal(_sid!);
        _checkTerminal(h, e);
        terminal = h;
        outputOffset = 0;
        terminalText = '';
        outputTruncated = false;
      }, feature: 'terminal', requireLive: true);
  Future<void> readTerminal() => _run((c, e) async {
        final h = terminal;
        if (h == null) return;
        await _identity(c, e);
        final v = await c.output(h, outputOffset);
        _checkTerminal(v.handle, e);
        if (v.handle.id != h.id || v.nextOffset < outputOffset) {
          throw const FormatException('Invalid terminal output');
        }
        terminal = v.handle;
        outputOffset = v.nextOffset;
        terminalText += v.output;
        outputTruncated |= v.truncated;
        if (terminalText.length > 200000) {
          terminalText = terminalText.substring(terminalText.length - 200000);
          outputTruncated = true;
        }
      }, feature: 'terminal', requireLive: true);
  Future<void> input(String text) => _run((c, e) async {
        final h = terminal;
        if (h == null || !h.running) throw StateError('No running terminal.');
        await _identity(c, e);
        await c.input(h, text);
        _check(e);
        notice = 'Input acknowledged. Refresh output to inspect the result.';
      }, feature: 'terminal', requireLive: true);
  Future<void> terminalAction(String action,
          {int columns = 80, int rows = 24}) =>
      _run((c, e) async {
        final h = terminal;
        if (h == null) return;
        await _identity(c, e);
        final ack = await switch (action) {
          'resize' => c.resize(h, columns, rows),
          'interrupt' => c.interrupt(h),
          _ => c.closeTerminal(h)
        };
        _checkTerminal(ack, e);
        if (ack.id != h.id) throw const FormatException('Wrong terminal');
        terminal = ack;
      }, feature: 'terminal', requireLive: true);
  Future<void> loadCheckpoints() => _run((c, e) async {
        final v = await c.checkpoints(_sid!);
        _check(e);
        checkpoints = v;
      }, feature: 'checkpoints', requireLive: true);
  Future<void> createCheckpoint(String label) => _run((c, e) async {
        await _identity(c, e);
        final ack = await c.createCheckpoint(_sid!, label);
        _check(e);
        checkpoints = List.unmodifiable([ack, ...checkpoints]);
        preview = null;
      }, feature: 'checkpoints', requireLive: true);
  Future<void> previewCheckpoint(FileCheckpoint cp) => _run((c, e) async {
        await _identity(c, e);
        final v = await c.preview(_sid!, cp.id);
        _check(e);
        if (v.data['checkpoint_id'] != cp.id) {
          throw const FormatException('Wrong checkpoint');
        }
        preview = v;
      }, feature: 'checkpoints', requireLive: true);
  Future<void> restoreCheckpoint(CheckpointPreview exact) => _run((c, e) async {
        if (!identical(preview, exact)) {
          throw StateError('Preview changed. Review it again.');
        }
        await _identity(c, e);
        await c.restore(_sid!, exact);
        _check(e);
        preview = null;
        notice =
            'Restore completed. A safety checkpoint was created. External actions were not undone.';
      }, feature: 'checkpoints', requireLive: true);
  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    session.removeListener(_observe);
    super.dispose();
  }
}
