import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';

/// Launches only the configured Python interpreter and fixed setup module.
class LocalSetupBackend implements SetupBackend {
  LocalSetupBackend(
      {String python = const String.fromEnvironment('ODESSY_SETUP_PYTHON'),
      String sourceRoot = const String.fromEnvironment('ODESSY_SETUP_ROOT'),
      this.timeout = const Duration(seconds: 30)})
      : python = python.isNotEmpty ? python : _bundledPython(),
        sourceRoot = sourceRoot.isNotEmpty ? sourceRoot : _bundledRoot();
  static String _bundledRoot() {
    var candidate = File(Platform.resolvedExecutable).parent;
    for (var depth = 0; depth < 7; depth++) {
      if (Directory('${candidate.path}${Platform.pathSeparator}bootstrap')
              .existsSync() &&
          Directory('${candidate.path}${Platform.pathSeparator}runtime')
              .existsSync()) {
        return candidate.path;
      }
      if (candidate.parent.path == candidate.path) break;
      candidate = candidate.parent;
    }
    return '';
  }

  static String _bundledPython() {
    final root = _bundledRoot();
    if (root.isEmpty) return 'python';
    final relative =
        Platform.isWindows ? 'runtime/python.exe' : 'runtime/bin/python3';
    return '$root${Platform.pathSeparator}$relative';
  }

  final String python, sourceRoot;
  final Duration timeout;
  Process? _process;
  Completer<void>? _settled;
  bool _closed = false;
  int _epoch = 0;

  Future<String> _read(Stream<List<int>> stream, int limit) async {
    final bytes = <int>[];
    await for (final chunk in stream) {
      if (bytes.length + chunk.length > limit) {
        throw const FormatException('Setup output exceeds its limit.');
      }
      bytes.addAll(chunk);
    }
    return utf8.decode(bytes);
  }

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (_closed || _settled != null) {
      throw StateError('Setup helper is unavailable or busy.');
    }
    final payload = utf8.encode('${jsonEncode(request.toJson())}\n');
    if (payload.length > 65536) {
      throw const FormatException('Setup request exceeds 64 KiB.');
    }
    final settled = Completer<void>();
    _settled = settled;
    final epoch = ++_epoch;
    Process? process;
    try {
      process = await Process.start(python, ['-m', 'odessy.setup.bridge'],
          workingDirectory: sourceRoot.isEmpty ? null : sourceRoot,
          environment: {
            'PYTHONUNBUFFERED': '1',
            if (sourceRoot.isNotEmpty)
              'PYTHONPATH':
                  '$sourceRoot${Platform.pathSeparator}${Directory('$sourceRoot${Platform.pathSeparator}bootstrap').existsSync() ? 'bootstrap' : 'src'}'
          },
          runInShell: false);
      _process = process;
      if (_closed || epoch != _epoch) throw const SetupCancelled();
      final stdout = _read(process.stdout, 4 * 1024 * 1024);
      final stderr = _read(process.stderr, 65536);
      process.stdin.add(payload);
      await process.stdin.close();
      final results = await Future.wait<Object>(
              [stdout, stderr, process.exitCode], eagerError: true)
          .timeout(request.operation == 'manage'
              ? const Duration(minutes: 12)
              : timeout);
      if (_closed || epoch != _epoch) throw const SetupCancelled();
      final value = jsonDecode(results[0] as String);
      if (value is! Json) {
        throw const FormatException('Invalid setup response.');
      }
      final reply = SetupReply.fromJson(value, request.id);
      if ((results[2] != 0) && reply.ok) {
        throw StateError('Setup helper failed.');
      }
      return reply;
    } finally {
      if (process != null) {
        process.kill();
        try {
          await process.exitCode.timeout(const Duration(seconds: 2));
        } on TimeoutException {
          process.kill(ProcessSignal.sigkill);
          await process.exitCode;
        }
      }
      if (identical(_process, process)) _process = null;
      if (identical(_settled, settled)) _settled = null;
      if (!settled.isCompleted) settled.complete();
    }
  }

  @override
  Future<void> cancel() async {
    ++_epoch;
    final process = _process;
    if (process != null) {
      if (Platform.isWindows) {
        await Process.run(
            'taskkill.exe', ['/PID', '${process.pid}', '/T', '/F']);
      } else {
        process.kill(ProcessSignal.sigterm);
      }
    }
    final pending = _settled;
    if (pending != null) await pending.future;
  }

  @override
  Future<void> close() async {
    _closed = true;
    await cancel();
  }
}
