import 'dart:io';

/// Keeps one foreground instance per installation and mode.
///
/// The lock is an OS advisory lock, so a crashed process releases it. Focusing the
/// existing window is a native runner concern; this only prevents a second window
/// from being created and reports that an instance already exists.
class InstanceGuard {
  InstanceGuard._(this._key, this._handle);

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
    final canonicalDir = await dir.resolveSymbolicLinks();
    final file = File('$canonicalDir${Platform.pathSeparator}$mode-$safe.lock');
    final lockKey = Platform.isWindows ? file.path.toLowerCase() : file.path;
    // POSIX advisory locks are process-wide. Do not open the same file twice:
    // closing a second descriptor would release the first descriptor's lock.
    if (!_held.add(lockKey)) return null;
    RandomAccessFile? handle;
    try {
      handle = await file.open(mode: FileMode.append);
      try {
        // Non-blocking: throws when another process holds the lock.
        await handle.lock(FileLock.exclusive);
      } on FileSystemException {
        await handle.close();
        _held.remove(lockKey);
        return null;
      }
      await handle.truncate(0);
      await handle.writeString('$pid');
      await handle.flush();
      return InstanceGuard._(lockKey, handle);
    } catch (_) {
      try {
        await handle?.close();
      } finally {
        _held.remove(lockKey);
      }
      rethrow;
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
    // Keep the file: deleting it after unlock can race with a new owner and
    // let a third process lock a different inode at the same path.
  }
}
