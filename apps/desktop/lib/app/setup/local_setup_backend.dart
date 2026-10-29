import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';

/// Launches only the configured Python interpreter and fixed setup module.
class LocalSetupBackend implements SetupBackend, SetupMetadataBackend {
  @override
  SetupBackend createMetadataReader() => LocalSetupBackend(
      python: python,
      sourceRoot: sourceRoot,
      payloadRoot: payloadRoot,
      timeout: timeout);
  LocalSetupBackend(
      {String python = const String.fromEnvironment('ODESSY_SETUP_PYTHON'),
      String sourceRoot = const String.fromEnvironment('ODESSY_SETUP_ROOT'),
      String? executablePath,
      this.payloadRoot = '',
      this.timeout = const Duration(seconds: 30)})
      : python = python.isNotEmpty
            ? python
            : (_bundledPython(executablePath) ?? _installedPython(executablePath) ??
                _sourcePython(sourceRoot.isNotEmpty ? sourceRoot : _sourceRoot(executablePath)) ?? 'python'),
        sourceRoot = sourceRoot.isNotEmpty ? sourceRoot :
            (_bundledRoot(executablePath).isNotEmpty ? _bundledRoot(executablePath) : _sourceRoot(executablePath));

  // A raw Flutter release remains a checkout build, not a portable distribution.
  // Locate only a verified ancestor project, never an arbitrary working folder.
  static String _sourceRoot(String? executablePath) {
    var candidate = File(executablePath ?? Platform.resolvedExecutable).parent;
    for (var depth = 0; depth < 12; depth++) {
      final project = File('${candidate.path}/pyproject.toml');
      try {
        if (project.existsSync() && project.lengthSync() <= 65536 &&
            RegExp(r'''^\s*name\s*=\s*["']odessy["']\s*$''', multiLine: true).hasMatch(project.readAsStringSync()) &&
            File('${candidate.path}/src/odessy/setup/bridge.py').existsSync()) {
          return candidate.path;
        }
      } on FileSystemException {
        return '';
      }
      if (candidate.parent.path == candidate.path) break;
      candidate = candidate.parent;
    }
    return '';
  }

  static String? _sourcePython(String root) {
    if (root.isEmpty) return null;
    final candidate = File('$root/.venv/${Platform.isWindows ? 'Scripts/python.exe' : 'bin/python3'}');
    return candidate.existsSync() ? candidate.path : null;
  }

  static String _bundledRoot([String? executablePath]) {
    var candidate = File(executablePath ?? Platform.resolvedExecutable).parent;
    for (var depth = 0; depth < 7; depth++) {
      if ((Directory('${candidate.path}${Platform.pathSeparator}bootstrap')
                  .existsSync() ||
              File('${candidate.path}${Platform.pathSeparator}portable-manifest.json')
                  .existsSync()) &&
          Directory('${candidate.path}${Platform.pathSeparator}runtime')
              .existsSync()) {
        return candidate.path;
      }
      if (candidate.parent.path == candidate.path) break;
      candidate = candidate.parent;
    }
    return '';
  }

  static String? _bundledPython([String? executablePath]) {
    final root = _bundledRoot(executablePath);
    if (root.isEmpty) return null;
    final relative =
        Platform.isWindows ? 'runtime/python.exe' : 'runtime/bin/python3';
    return '$root${Platform.pathSeparator}$relative';
  }

  /// An installed version keeps its owned interpreter beside the desktop build.
  static String? _installedPython([String? executablePath]) {
    final versionDirectory = File(executablePath ?? Platform.resolvedExecutable).parent.parent;
    final candidate = Platform.isWindows
        ? File(
            '${versionDirectory.path}${Platform.pathSeparator}Scripts${Platform.pathSeparator}python.exe')
        : File(
            '${versionDirectory.path}${Platform.pathSeparator}bin${Platform.pathSeparator}python3');
    return candidate.existsSync() ? candidate.path : null;
  }

  static String? _siblingLink() {
    final file=File('${File(Platform.resolvedExecutable).parent.path}/${Platform.isWindows ? 'odessy-link.exe' : 'odessy-link'}');
    return file.existsSync()?file.path:null;
  }

  static String defaultOutput() {
    final home = Platform.environment['HOME'] ??
        Platform.environment['USERPROFILE'] ??
        Directory.current.path;
    return '$home${Platform.pathSeparator}odessy-setup.json';
  }

  final String python, sourceRoot, payloadRoot;
  final Duration timeout;
  Process? _process;
  Directory? _cancelDirectory;
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
    final settled = Completer<void>();
    _settled = settled;
    final epoch = ++_epoch;
    Process? process;
    try {
      final message = request.toJson();
      if ({'installer', 'manage','component-probe'}.contains(request.operation)) {
        _cancelDirectory =
            await Directory.systemTemp.createTemp('odessy-setup-');
        message['cancel_file'] =
            '${_cancelDirectory!.path}${Platform.pathSeparator}cancel';
      }
      final payload = utf8.encode('${jsonEncode(message)}\n');
      if (payload.length > 65536) {
        throw const FormatException('Setup request exceeds 64 KiB.');
      }
      process = await Process.start(python, ['-m', 'odessy.setup.bridge'],
          workingDirectory: sourceRoot.isEmpty ? null : sourceRoot,
          environment: {
            'PYTHONUNBUFFERED': '1',
            if (_siblingLink() case final String link) 'ODESSY_LINK_BIN': link,
            if (payloadRoot.isNotEmpty) 'ODESSY_SETUP_PAYLOAD': payloadRoot,
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
          .timeout({'manage', 'installer'}.contains(request.operation)
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
      final directory = _cancelDirectory;
      _cancelDirectory = null;
      if (directory != null) {
        final marker = File('${directory.path}${Platform.pathSeparator}cancel');
        if (await marker.exists()) await marker.delete();
        await directory.delete();
      }
      if (!settled.isCompleted) settled.complete();
    }
  }

  @override
  Future<void> cancel() async {
    ++_epoch;
    final directory = _cancelDirectory;
    if (directory != null) {
      await File('${directory.path}${Platform.pathSeparator}cancel')
          .writeAsString('cancel');
      try {
        await _settled?.future.timeout(const Duration(seconds: 3));
        return;
      } on TimeoutException {/* Drain/kill the owned tree below. */}
    }
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
