import 'dart:io';

// Reproduces the mechanism behind
// apps/desktop/test/setup_usability_test.dart
//   'instance guard refuses a second window for the same mode'
// on Linux, without running the widget suite.
Future<void> main() async {
  final dir = await Directory.systemTemp.createTemp('lockprobe-');
  final file = File('${dir.path}/setup-abc.lock');

  final h1 = await file.open(mode: FileMode.write);
  await h1.lock(FileLock.exclusive);
  stdout.writeln('first acquire: OK');

  final h2 = await file.open(mode: FileMode.write);
  try {
    await h2.lock(FileLock.exclusive);
    stdout.writeln('second acquire (same process): SUCCEEDED');
  } on FileSystemException catch (e) {
    stdout.writeln('second acquire (same process): THREW ${e.osError?.message}');
  }

  await h1.unlock();
  await h1.close();
  await h2.close();
  await dir.delete(recursive: true);
}
