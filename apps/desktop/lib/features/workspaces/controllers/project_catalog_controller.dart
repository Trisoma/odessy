import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

class ProjectRecord {
  ProjectRecord.fromJson(Json value) {
    const keys = {
      'id',
      'label',
      'configuration_owner',
      'workspace',
      'service_origin',
      'service_instance_id',
      'session_ids'
    };
    if (value.keys.any((k) => !keys.contains(k)) ||
        value.length != keys.length) {
      throw const FormatException('Invalid project metadata fields');
    }
    String field(String key, int max) {
      final v = value[key];
      if (v is! String ||
          v.trim().isEmpty ||
          v.length > max ||
          v.runes.any((r) => r < 32)) {
        throw const FormatException('Invalid project metadata');
      }
      return v;
    }

    id = field('id', 128);
    label = field('label', 80).trim();
    configurationOwner = field('configuration_owner', 64);
    if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(configurationOwner)) {
      throw const FormatException('Invalid configuration owner');
    }
    workspace = field('workspace', 2048);
    serviceOrigin = checkedOrigin(field('service_origin', 256));
    serviceInstanceId = field('service_instance_id', 256);
    final ids = value['session_ids'];
    if (ids is! List ||
        ids.length > 1000 ||
        ids.any((v) =>
            v is! String ||
            v.isEmpty ||
            v.length > 256 ||
            v.runes.any((r) => r < 32)) ||
        ids.toSet().length != ids.length) {
      throw const FormatException('Invalid project session references');
    }
    sessionIds = List.unmodifiable(ids.cast<String>());
  }
  late final String id,
      label,
      configurationOwner,
      workspace,
      serviceOrigin,
      serviceInstanceId;
  late final List<String> sessionIds;
  Json toJson() => {
        'id': id,
        'label': label,
        'configuration_owner': configurationOwner,
        'workspace': workspace,
        'service_origin': serviceOrigin,
        'service_instance_id': serviceInstanceId,
        'session_ids': sessionIds
      };
  ProjectRecord copy({String? label, List<String>? sessionIds}) =>
      ProjectRecord.fromJson({
        ...toJson(),
        if (label != null) 'label': label,
        if (sessionIds != null) 'session_ids': sessionIds
      });
}

String checkedOrigin(String value) {
  final uri = Uri.tryParse(value);
  if (uri == null ||
      !['http', 'https'].contains(uri.scheme) ||
      !['localhost', '127.0.0.1', '::1', '[::1]'].contains(uri.host) ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      uri.path.isNotEmpty ||
      uri.origin != value) {
    throw const FormatException('Project service must be a loopback origin');
  }
  return value;
}

class ProjectCatalogSnapshot {
  ProjectCatalogSnapshot(List<ProjectRecord> records, this.revision)
      : records = List.unmodifiable(records) {
    if (revision < 0 ||
        records.length > 200 ||
        records.map((r) => r.id).toSet().length != records.length) {
      throw const FormatException('Invalid project catalog');
    }
  }
  final List<ProjectRecord> records;
  final int revision;
}

abstract interface class ProjectCatalogStore {
  Future<ProjectCatalogSnapshot> load();
  Future<ProjectCatalogSnapshot> save(List<ProjectRecord> records,
      {required int revision});
}

class ProjectCatalogConflict implements Exception {
  const ProjectCatalogConflict();
  @override
  String toString() => 'Project catalog changed. Reload before saving.';
}

class ProjectSessionSelection {
  const ProjectSessionSelection(
      this.serviceInstanceId, this.sessionId, this.engine, this.profile);
  final String serviceInstanceId, sessionId, engine, profile;
}

/// Grouping metadata never owns a transcript, grants, cwd or runtime lifecycle.
class ProjectCatalogController extends ChangeNotifier {
  ProjectCatalogController(
      {required this.store,
      required this.directory,
      required this.runtime,
      required this.api,
      required String serviceOrigin})
      : serviceOrigin = checkedOrigin(serviceOrigin);
  final ProjectCatalogStore store;
  final SessionDirectoryApi directory;
  final RuntimeConfigurationApi runtime;
  final OdessyApi api;
  final String serviceOrigin;
  ProjectCatalogSnapshot _snapshot = ProjectCatalogSnapshot([], 0);
  List<ProjectRecord> get records => _snapshot.records;
  List<SessionSummary> sessions = const [];
  RuntimeCapabilities? current;
  bool loading = false, saving = false;
  String? error;
  String query = '';
  int _epoch = 0;
  bool _disposed = false;
  List<SessionSummary> get filteredSessions => List.unmodifiable(sessions.where(
      (s) => '${s.id} ${s.engine} ${s.selectedProfile}'
          .toLowerCase()
          .contains(query.toLowerCase())));
  void search(String value) {
    query = value;
    notifyListeners();
  }

  bool matches(ProjectRecord project) {
    final context = current;
    return context != null &&
        project.serviceOrigin == serviceOrigin &&
        project.serviceInstanceId == context.serviceInstance &&
        project.configurationOwner == context.configuration?.owner &&
        project.workspace == context.configuration?.workspace;
  }

  String status(ProjectRecord project) => current == null
      ? 'Offline · session availability unknown'
      : matches(project)
          ? 'Current configured workspace'
          : 'Unavailable · service or configured workspace changed';

  Future<void> refresh() async {
    if (_disposed || saving) return;
    final epoch = ++_epoch;
    loading = true;
    current = null;
    sessions = const [];
    error = null;
    notifyListeners();
    try {
      final saved = await store.load();
      if (_disposed || epoch != _epoch) return;
      _snapshot =
          saved; // Metadata remains visible when the service is offline.
      final context = await runtime.runtimeCapabilities();
      final rows = <String, SessionSummary>{};
      int? offset = 0;
      while (offset != null) {
        final page = await directory.listSessions(limit: 100, offset: offset);
        if (page.serviceInstanceId != context.serviceInstance) {
          throw StateError(
              'Service restarted during discovery. Refresh again.');
        }
        for (final row in page.sessions) {
          rows[row.id] = row;
        }
        if (page.nextOffset != null && page.nextOffset! <= offset) {
          throw const FormatException('Session pagination did not advance');
        }
        offset = page.nextOffset;
        if (rows.length > 1000 || (offset != null && offset > 1000)) {
          throw StateError('Session catalog exceeds the desktop limit');
        }
      }
      if (_disposed || epoch != _epoch) return;
      current = context;
      sessions = List.unmodifiable(rows.values);
    } catch (_) {
      if (!_disposed && epoch == _epoch) {
        error =
            'Could not refresh projects and live sessions. Reload to retry.';
      }
    } finally {
      if (!_disposed && epoch == _epoch) {
        loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> _save(List<ProjectRecord> next) async {
    if (_disposed || loading || saving) throw StateError('Catalog unavailable');
    saving = true;
    error = null;
    notifyListeners();
    try {
      final saved = await store.save(next, revision: _snapshot.revision);
      if (!_disposed) _snapshot = saved;
    } catch (_) {
      if (!_disposed) {
        error =
            'Project metadata was not saved. Reload to check for conflicts.';
      }
    } finally {
      if (!_disposed) {
        saving = false;
        notifyListeners();
      }
    }
  }

  Future<void> registerCurrent(String label, {String? sessionId}) async {
    final context = current;
    if (context?.configuration == null ||
        context!.configuration!.workspace.isEmpty ||
        loading ||
        saving) {
      throw StateError('No verified configured workspace');
    }
    final epoch = _epoch;
    final fresh = await runtime.runtimeCapabilities();
    if (_disposed ||
        epoch != _epoch ||
        api.identity != serviceOrigin ||
        fresh.serviceInstance != context.serviceInstance ||
        fresh.configuration?.owner != context.configuration!.owner ||
        fresh.configuration?.workspace != context.configuration!.workspace) {
      throw StateError(
          'Configured workspace changed. Refresh before registering.');
    }
    if (sessionId != null && !sessions.any((s) => s.id == sessionId)) {
      throw StateError('Session is no longer available');
    }
    final random = Random.secure();
    final record = ProjectRecord.fromJson({
      'id': List.generate(
              16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
          .join(),
      'label': label,
      'configuration_owner': context.configuration!.owner,
      'workspace': context.configuration!.workspace,
      'service_origin': serviceOrigin,
      'service_instance_id': context.serviceInstance,
      'session_ids': [if (sessionId != null) sessionId]
    });
    if (records.any(matches)) {
      throw StateError('The configured workspace is already registered');
    }
    await _save([...records, record]);
  }

  Future<void> rename(String id, String label) async {
    if (!records.any((r) => r.id == id)) {
      throw StateError('Project no longer exists');
    }
    await _save(
        [for (final r in records) r.id == id ? r.copy(label: label) : r]);
  }

  Future<void> groupSession(String projectId, String sid) async {
    final project = records.firstWhere((p) => p.id == projectId);
    if (!matches(project) || !sessions.any((s) => s.id == sid)) {
      throw StateError('Only current owned sessions can be grouped');
    }
    await _save([
      for (final r in records)
        r.copy(sessionIds: [
          ...r.sessionIds.where((id) => id != sid),
          if (r.id == projectId) sid
        ])
    ]);
  }

  Future<void> removeGroup(String id) =>
      _save(records.where((r) => r.id != id).toList());

  /// Revalidates immediately before returning an identity to the shell. The shell
  /// still owns busy/voice guards and must retain other live sessions on selection.
  Future<ProjectSessionSelection> select(String projectId, String sid) async {
    if (_disposed || loading || saving) throw StateError('Catalog unavailable');
    final epoch = _epoch;
    final project = records.firstWhere((p) => p.id == projectId);
    if (!matches(project) || !project.sessionIds.contains(sid)) {
      throw StateError('Session unavailable');
    }
    final context = await runtime.runtimeCapabilities();
    SessionSummary? summary;
    int? offset = 0;
    while (offset != null) {
      final page = await directory.listSessions(limit: 100, offset: offset);
      if (page.serviceInstanceId != context.serviceInstance) {
        throw StateError('Service changed');
      }
      for (final row in page.sessions) {
        if (row.id == sid) summary = row;
      }
      if (page.nextOffset != null &&
          (page.nextOffset! <= offset || page.nextOffset! > 1000)) {
        throw StateError('Session catalog changed');
      }
      offset = page.nextOffset;
    }
    if (summary == null) throw StateError('Session no longer exists');
    final snapshot = await api.snapshot(sid);
    final confirmed = await runtime.runtimeCapabilities();
    if (_disposed ||
        epoch != _epoch ||
        api.identity != serviceOrigin ||
        confirmed.serviceInstance != context.serviceInstance ||
        confirmed.configuration?.owner != context.configuration?.owner ||
        confirmed.configuration?.workspace !=
            context.configuration?.workspace ||
        context.serviceInstance != project.serviceInstanceId ||
        context.configuration?.owner != project.configurationOwner ||
        context.configuration?.workspace != project.workspace ||
        snapshot.id != sid ||
        snapshot.selectedProfile != summary.selectedProfile ||
        snapshot.running) {
      throw StateError(
          'Session context changed or is busy. Refresh before opening.');
    }
    return ProjectSessionSelection(
        context.serviceInstance, sid, summary.engine, summary.selectedProfile);
  }

  void invalidate() {
    ++_epoch;
    current = null;
    sessions = const [];
    loading = false;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    super.dispose();
  }
}
