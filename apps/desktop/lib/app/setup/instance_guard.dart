import 'dart:io';

/// Keeps one foreground instance per installation and mode.
///
/// The lock is an OS advisory lock, so a crashed process releases it. Focusing the
/// existing window is a native runner concern; this only prevents a second window
/// from being created and reports that an instance already exists.
class InstanceGuard {
  InstanceGuard._(this._key, this._handle);

  // POSIX locks belong to the process, so the OS alone does not reject a
  // second acquisition in this isolate. Reserve before awaiting file open.
  static final Set<String> _held = {};
  final String _key;
  final RandomAccessFile _handle;
  bool _released = false;

  static Future<InstanceGuard?> acquire(
    String key, {
    Directory? directory,
    String mode = 'setup',
  }) async {
    final dir = directory ??
        Directory(
            '${Platform.environment['LOCALAPPDATA'] ?? Directory.systemTemp.path}'
            '${Platform.pathSeparator}Odessy${Platform.pathSeparator}instances');
    await dir.create(recursive: true);
    final safe = key.replaceAll(RegExp('[^A-Za-z0-9_-]'), '_');
    final root = await dir.resolveSymbolicLinks();
    final file = File('$root${Platform.pathSeparator}$mode-$safe.lock');
    final identity = Platform.isWindows ? file.path.toLowerCase() : file.path;
    if (!_held.add(identity)) return null;
    RandomAccessFile? handle;
    var acquired = false;
    try {
      // Append opens without truncating another process's owner record.
      handle = await file.open(mode: FileMode.append);
      try {
        await handle.lock(FileLock.exclusive);
      } on FileSystemException {
        return null;
      }
      await handle.truncate(0);
      await handle.setPosition(0);
      await handle.writeString('$pid');
      await handle.flush();
      acquired = true;
      return InstanceGuard._(identity, handle);
    } finally {
      if (!acquired) {
        try {
          await handle?.close();
        } finally {
          _held.remove(identity);
        }
      }
    }
  }

  Future<void> release() async {
    if (_released) return;
    _released = true;
    try {
      await _handle.unlock();
    } on FileSystemException {
      // Already released by process exit.
    } finally {
      try {
        await _handle.close();
      } finally {
        _held.remove(_key);
      }
    }
    // Keep the inode: unlinking after unlock can let concurrent owners lock
    // different files with the same name. A stale PID is not a held OS lock.
  }
}
