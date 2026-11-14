import 'dart:convert';
import 'dart:io';
import '../setup/instance_guard.dart';
import 'local_preferences_store.dart';
import 'shell_layout.dart';

export 'shell_layout.dart';

/// Independent file and writer lock: existing appearance preferences stay intact.
class LocalShellLayoutStore implements ShellLayoutStore {
  LocalShellLayoutStore(this.directory);
  final Directory directory;
  static const maxBytes = 4096;
  static LocalShellLayoutStore forUser() =>
      LocalShellLayoutStore(LocalPreferencesStore.forUser().directory);
  File get _file =>
      File('${directory.path}${Platform.pathSeparator}shell-layout.json');

  @override
  Future<ShellLayoutSnapshot> load() async {
    final type = await FileSystemEntity.type(_file.path, followLinks: false);
    if (type == FileSystemEntityType.notFound) {
      return const ShellLayoutSnapshot(ShellLayout(), 0);
    }
    if (type != FileSystemEntityType.file) {
      throw const FormatException('Invalid shell layout file');
    }
    final handle = await _file.open();
    late final List<int> bytes;
    try {
      bytes = await handle.read(maxBytes + 1);
    } finally {
      await handle.close();
    }
    if (bytes.length > maxBytes) {
      throw const FormatException('Shell layout file exceeds limit');
    }
    final data = jsonDecode(utf8.decode(bytes));
    if (data is! Map<String, dynamic> ||
        data.length != 3 ||
        data.keys.any((key) =>
            !const {'schema_version', 'revision', 'layout'}.contains(key)) ||
        data['schema_version'] != 1 ||
        data['revision'] is! int ||
        (data['revision'] as int) < 1 ||
        data['layout'] is! Map<String, dynamic>) {
      throw const FormatException('Unsupported shell layout document');
    }
    return ShellLayoutSnapshot(
        ShellLayout.fromJson(data['layout'] as Map<String, dynamic>),
        data['revision'] as int);
  }

  @override
  Future<ShellLayoutSnapshot> save(ShellLayout value,
      {required int revision}) async {
    ShellLayout.fromJson(value.toJson());
    final guard = await InstanceGuard.acquire('shell-layout',
        directory: directory, mode: 'writer');
    if (guard == null) throw const ShellLayoutConflict();
    File? temporary;
    try {
      final current = await load();
      if (revision != current.revision) throw const ShellLayoutConflict();
      final next = ShellLayoutSnapshot(value, revision + 1);
      temporary = File(
          '${_file.path}.$pid.${DateTime.now().microsecondsSinceEpoch}.tmp');
      await temporary.writeAsString(
          jsonEncode({
            'schema_version': 1,
            'revision': next.revision,
            'layout': value.toJson()
          }),
          flush: true);
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
