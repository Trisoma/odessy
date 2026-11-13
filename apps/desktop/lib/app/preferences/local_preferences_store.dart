import 'dart:convert';
import 'dart:io';
import '../setup/instance_guard.dart';
import 'desktop_preferences.dart';

/// App-owned preferences, independent of installed payloads and setup exports.
/// Callers may inject a temporary directory; constructors perform no I/O.
class LocalPreferencesStore implements DesktopPreferencesStore {
  LocalPreferencesStore(this.directory);
  final Directory directory;
  static const maxBytes = 16384;
  File get _file =>
      File('${directory.path}${Platform.pathSeparator}preferences.json');

  static LocalPreferencesStore forUser() {
    final env = Platform.environment;
    final home = env['HOME'] ?? env['USERPROFILE'];
    final String? root = Platform.isWindows
        ? env['LOCALAPPDATA']
        : Platform.isMacOS
            ? (home == null ? null : '$home/Library/Application Support')
            : (env['XDG_CONFIG_HOME'] ??
                (home == null ? null : '$home/.config'));
    if (root == null || root.isEmpty) {
      throw StateError('User preference directory unavailable.');
    }
    return LocalPreferencesStore(Directory(
        '$root${Platform.pathSeparator}Odessy${Platform.pathSeparator}desktop'));
  }

  @override
  Future<PreferencesSnapshot> load() async {
    final type = await FileSystemEntity.type(_file.path, followLinks: false);
    if (type == FileSystemEntityType.notFound) {
      return const PreferencesSnapshot(DesktopPreferences(), 0);
    }
    if (type != FileSystemEntityType.file) {
      throw const FormatException('Invalid preferences file');
    }
    final handle = await _file.open();
    late final List<int> bytes;
    try {
      bytes = await handle.read(maxBytes + 1);
    } finally {
      await handle.close();
    }
    if (bytes.length > maxBytes) {
      throw const FormatException('Preferences file exceeds limit');
    }
    final data = jsonDecode(utf8.decode(bytes));
    if (data is! Map<String, dynamic> ||
        data['schema_version'] != 1 ||
        data['revision'] is! int ||
        (data['revision'] as int) < 1 ||
        data['preferences'] is! Map<String, dynamic> ||
        data.keys.any((key) => !const {
              'schema_version',
              'revision',
              'preferences'
            }.contains(key))) {
      throw const FormatException('Unsupported preferences document');
    }
    return PreferencesSnapshot(
        DesktopPreferences.fromJson(
            data['preferences'] as Map<String, dynamic>),
        data['revision'] as int);
  }

  @override
  Future<PreferencesSnapshot> save(DesktopPreferences value,
      {required int revision}) async {
    // Validate even values constructed directly, before creating any files.
    DesktopPreferences.fromJson(value.toJson());
    final guard = await InstanceGuard.acquire('preferences',
        directory: directory, mode: 'writer');
    if (guard == null) throw const PreferencesConflict();
    File? temporary;
    try {
      final current = await load();
      if (current.revision != revision) throw const PreferencesConflict();
      final next = PreferencesSnapshot(value, revision + 1);
      temporary = File(
          '${_file.path}.$pid.${DateTime.now().microsecondsSinceEpoch}.tmp');
      await temporary.writeAsString(
          jsonEncode({
            'schema_version': 1,
            'revision': next.revision,
            'preferences': value.toJson()
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
