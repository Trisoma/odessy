import 'dart:io';

/// Keeps one foreground instance per installation and mode.
///
/// The lock is an OS advisory lock, so a crashed process releases it. Focusing the
/// existing window is a native runner concern; this only prevents a second window
/// from being created and reports that an instance already exists.
class InstanceGuard {
  InstanceGuard._(this._file, this._handle);

  final File _file;
  final RandomAccessFile _handle;

  static Future<InstanceGuard?> acquire(
    String key, {
    Directory? directory,
    String mode = 'setup',
  }) async {
    final dir = directory ??
        Directory('${Platform.environment['LOCALAPPDATA'] ?? Directory.systemTemp.path}'
            '${Platform.pathSeparator}Odessy${Platform.pathSeparator}instances');
    await dir.create(recursive: true);
    final safe = key.replaceAll(RegExp('[^A-Za-z0-9_-]'), '_');
    final file = File('${dir.path}${Platform.pathSeparator}$mode-$safe.lock');
    final handle = await file.open(mode: FileMode.write);
    try {
      // Non-blocking: throws when another process holds the lock.
      await handle.lock(FileLock.exclusive);
    } on FileSystemException {
      await handle.close();
      return null;
    }
    await handle.truncate(0);
    await handle.writeString('$pid');
    await handle.flush();
    return InstanceGuard._(file, handle);
  }

  Future<void> release() async {
    try {
      await _handle.unlock();
    } on FileSystemException {
      // Already released by process exit.
    }
    await _handle.close();
    try {
      if (await _file.exists()) await _file.delete();
    } on FileSystemException {
      // Leftover lock files are harmless.
    }
  }
}
