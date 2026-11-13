import 'dart:convert';
import 'dart:io';
import '../../features/workspaces/controllers/project_catalog_controller.dart';
import '../preferences/local_preferences_store.dart';
import '../setup/instance_guard.dart';

/// Per-user grouping references only. No engine history, bearer or grants.
class LocalProjectStore implements ProjectCatalogStore {
  LocalProjectStore(this.directory);
  factory LocalProjectStore.forUser() =>
      LocalProjectStore(LocalPreferencesStore.forUser().directory);
  final Directory directory;
  static const maxBytes = 512 * 1024;
  File get _file =>
      File('${directory.path}${Platform.pathSeparator}projects.json');

  @override
  Future<ProjectCatalogSnapshot> load() async {
    final type = await FileSystemEntity.type(_file.path, followLinks: false);
    if (type == FileSystemEntityType.notFound) {
      return ProjectCatalogSnapshot([], 0);
    }
    if (type != FileSystemEntityType.file) {
      throw const FormatException('Invalid project catalog file');
    }
    final handle = await _file.open();
    late final List<int> bytes;
    try {
      bytes = await handle.read(maxBytes + 1);
    } finally {
      await handle.close();
    }
    if (bytes.length > maxBytes) {
      throw const FormatException('Project catalog exceeds limit');
    }
    final data = jsonDecode(utf8.decode(bytes));
    if (data is! Map<String, dynamic> ||
        data['schema_version'] != 1 ||
        data['revision'] is! int ||
        (data['revision'] as int) < 1 ||
        data['projects'] is! List ||
        (data['projects'] as List).any((r) => r is! Map<String, dynamic>) ||
        data.keys.any(
            (k) => !{'schema_version', 'revision', 'projects'}.contains(k))) {
      throw const FormatException('Unsupported project catalog');
    }
    return ProjectCatalogSnapshot(
        (data['projects'] as List)
            .map((r) => ProjectRecord.fromJson(r as Map<String, dynamic>))
            .toList(),
        data['revision'] as int);
  }

  @override
  Future<ProjectCatalogSnapshot> save(List<ProjectRecord> records,
      {required int revision}) async {
    // Validate before any disk mutation, including direct typed construction.
    final next = ProjectCatalogSnapshot(
        records.map((r) => ProjectRecord.fromJson(r.toJson())).toList(),
        revision + 1);
    final text = jsonEncode({
      'schema_version': 1,
      'revision': next.revision,
      'projects': next.records.map((r) => r.toJson()).toList()
    });
    if (utf8.encode(text).length > maxBytes) {
      throw const FormatException('Project catalog exceeds limit');
    }
    final guard = await InstanceGuard.acquire('projects',
        directory: directory, mode: 'writer');
    if (guard == null) throw const ProjectCatalogConflict();
    File? temporary;
    try {
      if ((await load()).revision != revision) {
        throw const ProjectCatalogConflict();
      }
      temporary = File(
          '${_file.path}.$pid.${DateTime.now().microsecondsSinceEpoch}.tmp');
      await temporary.writeAsString(text, flush: true);
      await temporary.rename(_file.path);
      temporary = null;
      return next;
    } finally {
      try {
        if (temporary != null && await temporary.exists()) {
          await temporary.delete();
        }
      } finally {
        await guard.release();
      }
    }
  }
}
