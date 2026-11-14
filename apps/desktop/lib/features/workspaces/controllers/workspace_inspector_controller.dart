import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

enum InspectorPanel { files, terminal, changes }

class WorkspaceInspectorController extends ChangeNotifier {
  WorkspaceInspectorController(
      {required this.api,
      required this.sessionApi,
      required this.serviceIdentity,
      this.resolveApproval,
      this.validateProposal});
  final WorkspaceInspectorApi api;
  final OdessyApi sessionApi;
  final String serviceIdentity;
  final Future<void> Function(String sid, String requestId, bool approved)?
      resolveApproval;

  /// Integration supplies the real bound proposal check; absence means preview only.
  final Future<bool> Function(ProposedChange proposal)? validateProposal;
  WorkspaceDescriptor? descriptor;
  WorkspaceTree? tree;
  WorkspaceTextFile? file;
  List<RecordedCommandOutput> outputs = const [];
  List<ProposedChange> changes = const [];
  List<WorkspaceAttachment> attachments = const [];
  Set<String> _pending = {};
  bool loading = false, reading = false, deciding = false;
  String? error;
  InspectorPanel panel = InspectorPanel.files;
  int _epoch = 0, _readEpoch = 0;
  int _attachmentEpoch = 0;
  Session? _snapshot;
  bool _disposed = false;
  String? _sid, _instance;

  bool _current(int epoch) =>
      !_disposed && epoch == _epoch && sessionApi.identity == serviceIdentity;
  void show(InspectorPanel value) {
    panel = value;
    notifyListeners();
  }

  void _clear() {
    ++_attachmentEpoch;
    _snapshot = null;
    descriptor = null;
    tree = null;
    file = null;
    outputs = const [];
    changes = const [];
    attachments = const [];
    _pending = {};
  }

  Future<void> bind(
      {required String sessionId, required String serviceInstanceId}) async {
    final sameSession = _sid == sessionId && _instance == serviceInstanceId;
    final previous = sameSession ? descriptor : null;
    final epoch = ++_epoch;
    ++_readEpoch;
    _sid = sessionId;
    _instance = serviceInstanceId;
    if (!sameSession) _clear();
    loading = true;
    reading = deciding = false;
    error = null;
    notifyListeners();
    try {
      final context = await api.workspace(sessionId);
      if (!_current(epoch)) return;
      if (context.sessionId != sessionId ||
          context.serviceInstanceId != serviceInstanceId) {
        throw StateError('Workspace context changed');
      }
      if (previous == null ||
          !previous.sameContext(context) ||
          !context.readFiles) {
        _clear();
      }
      descriptor = context;
      if (context.readFiles) {
        final listing = await api.listWorkspace(sessionId);
        if (!_current(epoch)) return;
        _check(listing);
        tree = listing;
      }
      if (context.recordedOutput) {
        final snapshot = await sessionApi.snapshot(sessionId);
        if (!_current(epoch)) return;
        acceptSnapshot(snapshot, serviceInstanceId: context.serviceInstanceId);
      }
    } catch (_) {
      if (_current(epoch)) {
        _clear();
        error = 'Workspace unavailable or access denied. Refresh to retry.';
      }
    } finally {
      if (_current(epoch)) {
        loading = false;
        notifyListeners();
      }
    }
  }

  void _check(WorkspaceContext response) {
    if (descriptor == null || !descriptor!.sameContext(response)) {
      throw StateError('Workspace context changed');
    }
  }

  Future<void> open(String path, {required bool directory}) async {
    if (_disposed || descriptor?.readFiles != true || loading) return;
    final epoch = _epoch, request = ++_readEpoch, sid = _sid!;
    reading = true;
    error = null;
    file = null;
    notifyListeners();
    try {
      // Recheck capabilities/context before each read; the service rechecks policy.
      final fresh = await api.workspace(sid);
      if (!_current(epoch) || request != _readEpoch) return;
      _check(fresh);
      if (!fresh.readFiles) throw StateError('Workspace permission revoked');
      if (directory) {
        final result = await api.listWorkspace(sid, path: path);
        if (!_current(epoch) || request != _readEpoch) return;
        _check(result);
        tree = result;
      } else {
        final result = await api.readWorkspaceFile(sid, path);
        if (!_current(epoch) || request != _readEpoch) return;
        _check(result);
        file = result;
      }
    } catch (_) {
      if (_current(epoch) && request == _readEpoch) {
        _clear();
        error =
            'Cannot inspect this path. It may be denied, missing, binary, too large or changed.';
      }
    } finally {
      if (_current(epoch) && request == _readEpoch) {
        reading = false;
        notifyListeners();
      }
    }
  }

  void acceptSnapshot(Session incoming, {required String serviceInstanceId}) {
    if (_disposed ||
        descriptor == null ||
        incoming.id != _sid ||
        serviceInstanceId != _instance ||
        sessionApi.identity != serviceIdentity) {
      return;
    }
    final snapshot = reduceSessionSnapshot(_snapshot, incoming);
    _snapshot = snapshot;
    _pending = snapshot.pending
        .where((p) => p['kind'] == 'approval' && p['id'] is String)
        .map((p) => p['id'] as String)
        .toSet();
    final projected = <ProposedChange>[];
    for (final pending in snapshot.pending) {
      final value = pending['proposed_change'];
      if (pending['kind'] != 'approval' || value is! Json) continue;
      try {
        final proposal =
            ProposedChange.fromJson({...value, 'request_id': pending['id']});
        if (proposal.sessionId == _sid &&
            _pending.contains(proposal.requestId)) {
          projected.add(proposal);
        }
      } on FormatException {
        // Invalid/redacted content cannot become an actionable diff.
      }
    }
    changes = List.unmodifiable(projected.isNotEmpty
        ? projected
        : changes.where((p) => _pending.contains(p.requestId)));
    outputs = descriptor!.recordedOutput
        ? recordedSessionOutputs(snapshot)
        : const [];
    notifyListeners();
  }

  void acceptChanges(List<ProposedChange> proposals) {
    if (_disposed || descriptor == null) return;
    if (proposals.length > 64 ||
        proposals.any(
            (p) => p.sessionId != _sid || !_pending.contains(p.requestId)) ||
        proposals.map((p) => p.requestId).toSet().length != proposals.length) {
      throw StateError('Proposal is not an owned pending approval');
    }
    changes = List.unmodifiable(proposals);
    notifyListeners();
  }

  bool canDecide(ProposedChange proposal) =>
      !_disposed &&
      sessionApi.identity == serviceIdentity &&
      descriptor?.proposedChanges == true &&
      resolveApproval != null &&
      validateProposal != null &&
      !deciding &&
      changes.contains(proposal) &&
      _pending.contains(proposal.requestId);

  Future<void> decide(ProposedChange proposal, bool approved) async {
    if (!canDecide(proposal)) {
      throw StateError('Exact approval integration unavailable');
    }
    final epoch = _epoch;
    deciding = true;
    error = null;
    notifyListeners();
    try {
      if (!await validateProposal!(proposal)) {
        throw StateError('Proposal changed');
      }
      if (!_current(epoch) || !_pending.contains(proposal.requestId)) return;
      await resolveApproval!(proposal.sessionId, proposal.requestId, approved);
      if (!_current(epoch)) return;
      _pending.remove(proposal.requestId);
      changes = List.unmodifiable(
          changes.where((p) => p.requestId != proposal.requestId));
    } catch (_) {
      if (_current(epoch)) {
        error =
            'Approval was not confirmed. Refresh the exact request before retrying.';
      }
    } finally {
      if (_current(epoch)) {
        deciding = false;
        notifyListeners();
      }
    }
  }

  void attachCurrent() {
    final selected = file;
    if (selected == null ||
        descriptor == null ||
        !descriptor!.sameContext(selected)) {
      throw StateError('Select and review a current text file');
    }
    final next = [
      ...attachments.where((a) => a.file.path != selected.path),
      WorkspaceAttachment(selected)
    ];
    if (next.fold<int>(
            0,
            (sum, a) =>
                sum +
                utf8.encode(a.file.text).length +
                utf8.encode(a.file.path).length +
                200) >
        32 * 1024) {
      throw StateError('Combined text attachments exceed 32 KiB');
    }
    attachments = List.unmodifiable(next);
    ++_attachmentEpoch;
    notifyListeners();
  }

  void removeAttachment(String path) {
    ++_attachmentEpoch;
    attachments =
        List.unmodifiable(attachments.where((a) => a.file.path != path));
    notifyListeners();
  }

  Future<String> reviewedAttachmentText() async {
    final context = descriptor;
    if (context == null) throw StateError('Workspace unavailable');
    final epoch = _epoch;
    final attachmentsEpoch = _attachmentEpoch;
    final text = await revalidateAttachments(api, context, attachments);
    if (!_current(epoch) ||
        descriptor != context ||
        attachmentsEpoch != _attachmentEpoch) {
      throw StateError('Attachment context changed');
    }
    return text;
  }

  Future<void> refresh() async {
    if (_sid != null && _instance != null) {
      await bind(sessionId: _sid!, serviceInstanceId: _instance!);
    }
  }

  void invalidate() {
    ++_epoch;
    ++_readEpoch;
    _clear();
    loading = reading = deciding = false;
    error = 'Disconnected · workspace access is unavailable';
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    ++_readEpoch;
    super.dispose();
  }
}
