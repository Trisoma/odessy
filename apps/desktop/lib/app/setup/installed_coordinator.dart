import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:odessy_client/odessy_client.dart';
import 'session_model_credentials.dart';

/// An installed coordinator that could not be started. The message is shown to
/// the user; it never contains the bearer token.
class CoordinatorStartFailure implements Exception {
  const CoordinatorStartFailure(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The lifecycle the startup controller owns; a test can supply its own.
abstract interface class InstalledCoordinatorHandle {
  Future<LocalApi> start();
  Future<void> stop();
}

/// Owns one authenticated loopback coordinator started from installed files.
///
/// The desktop is the only owner: closing the application closes the child's
/// stdin, which makes the coordinator exit rather than outlive its launcher.
class InstalledCoordinator implements InstalledCoordinatorHandle {
  InstalledCoordinator({
    required this.python,
    required this.root,
    required this.workspace,
    this.configuration,
    this.expectedFingerprint,
    String? token,
    this.readyTimeout = const Duration(seconds: 45),
    Future<int> Function()? portPicker,
    Future<Process> Function(String python, List<String> arguments, String? cwd,
            Map<String, String> environment)?
        launcher,
    Future<bool> Function(Uri base, String token)? probe,
  })  : token = token ?? newToken(),
        portPicker = portPicker ?? _freeLoopbackPort,
        launcher = launcher ?? _startProcess,
        probe = probe ?? _probeLoopback;

  final String python, root, workspace, token;
  final Json? configuration;
  final String? expectedFingerprint;
  final Duration readyTimeout;
  final Future<int> Function() portPicker;
  final Future<Process> Function(String python, List<String> arguments,
      String? cwd, Map<String, String> environment) launcher;
  final Future<bool> Function(Uri base, String token) probe;

  Process? _process;
  int? _exitCode;
  String _stderr = '';
  int? port;
  final Map<int, Completer<void>> _credentialAcks = {};
  String _ackBuffer = '';

  Uri? get base => port == null ? null : Uri.parse('http://127.0.0.1:$port');
  bool get running => _process != null && _exitCode == null;

  static String newToken() {
    final random = Random.secure();
    return base64Url
        .encode(List<int>.generate(32, (_) => random.nextInt(256)))
        .replaceAll('=', '');
  }

  static Future<int> _freeLoopbackPort() async {
    final socket = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final value = socket.port;
    await socket.close();
    return value;
  }

  static Future<Process> _startProcess(String python, List<String> arguments,
          String? cwd, Map<String, String> environment) =>
      Process.start(python, arguments,
          workingDirectory: cwd, environment: environment, runInShell: false);

  static Future<bool> _probeLoopback(Uri base, String token) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 2);
    try {
      final request = await client.getUrl(base.resolve('/v1/profiles'));
      request.followRedirects = false;
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
      final response =
          await request.close().timeout(const Duration(seconds: 4));
      await response.drain<void>();
      return response.statusCode == 200;
    } catch (_) {
      return false;
    } finally {
      client.close(force: true);
    }
  }

  /// The recorded workspace may not exist yet; the coordinator creates it after
  /// startup, so an absent directory must not block process creation.
  static String? launchDirectory(String workspace) {
    if (workspace.isEmpty) return null;
    return Directory(workspace).existsSync() ? workspace : null;
  }

  /// Starts the coordinator and waits until it answers an authenticated probe.
  @override
  Future<LocalApi> start() async {
    if (_process != null) {
      throw StateError('The coordinator is already running.');
    }
    final chosen = await portPicker();
    _exitCode = null;
    _stderr = '';
    _ackBuffer = '';
    final Process process;
    try {
      process = await launcher(
          python,
          [
            '-m',
            if (configuration == null) ...[
              'odessy.setup.installed',
              '--root',
              root
            ] else ...[
              'odessy.setup.configured',
              '--config',
              configuration!['config'] as String,
              '--format',
              configuration!['format'] as String,
              '--selection-json',
              jsonEncode(configuration!['selection']),
              '--sha256',
              configuration!['sha256'] as String,
              '--workspace',
              workspace,
            ],
            '--port',
            '$chosen',
            if (expectedFingerprint != null) ...[
              '--expected-fingerprint',
              expectedFingerprint!
            ],
            '--exit-on-stdin-close',
            '--credentials-stdin'
          ],
          launchDirectory(workspace),
          {
            'ODESSY_API_TOKEN': token,
            'PYTHONUNBUFFERED': '1',
            if (configuration?['pythonpath'] is String)
              'PYTHONPATH': configuration!['pythonpath'] as String,
          });
    } on ProcessException catch (failure) {
      throw CoordinatorStartFailure(
          'The installed interpreter could not be started: ${failure.message}');
    }
    _process = process;
    sessionModelCredentials.addListener(_sendCredentials);
    process.exitCode.then((code) => _exitCode = code);
    process.stdout.transform(const Utf8Decoder(allowMalformed: true)).listen(_readCredentialAcks);
    process.stderr
        .transform(const Utf8Decoder(allowMalformed: true))
        .listen((chunk) {
      if (_stderr.length < 8192) _stderr += chunk;
    });
    try {
      await _sendCredentials();
    } catch (_) {
      await stop();
      throw const CoordinatorStartFailure('The coordinator did not acknowledge its private credential startup message.');
    }
    final target = Uri.parse('http://127.0.0.1:$chosen');
    final deadline = DateTime.now().add(readyTimeout);
    while (DateTime.now().isBefore(deadline)) {
      if (_exitCode != null) {
        throw CoordinatorStartFailure(
            'The local coordinator stopped before it became ready.${_detail()}');
      }
      if (await probe(target, token)) {
        port = chosen;
        return LocalApi(target, token);
      }
      await Future<void>.delayed(const Duration(milliseconds: 250));
    }
    await stop();
    throw CoordinatorStartFailure(
        'The local coordinator did not answer within ${readyTimeout.inSeconds} seconds.${_detail()}');
  }

  String _detail() {
    final text = _stderr.trim();
    if (text.isEmpty) {
      return _exitCode == null ? '' : ' (exit code $_exitCode)';
    }
    final tail = text.length > 500 ? text.substring(text.length - 500) : text;
    return ' ${tail.replaceAll('\n', ' ')}';
  }

  Future<void> _sendCredentials() async {
    final process = _process;
    if (process == null || _exitCode != null) {
      throw StateError('The owned coordinator is no longer running.');
    }
    final revision = sessionModelCredentials.revision;
    final ack = Completer<void>();
    _credentialAcks[revision] = ack;
    try {
      await (() async {
        process.stdin.writeln(jsonEncode({'session_credentials': sessionModelCredentials.privateStdinValues,
          'credential_revision': revision}));
        await process.stdin.flush();
        await ack.future;
      })().timeout(const Duration(seconds: 5));
    } finally {
      _credentialAcks.remove(revision);
      if (!ack.isCompleted) ack.complete();
    }
  }

  void _readCredentialAcks(String chunk) {
    _ackBuffer += chunk;
    if (_ackBuffer.length > 65536) { _ackBuffer = ''; return; }
    while (_ackBuffer.contains('\n')) {
      final index = _ackBuffer.indexOf('\n');
      final line = _ackBuffer.substring(0, index);
      _ackBuffer = _ackBuffer.substring(index + 1);
      try {
        final value = jsonDecode(line);
        final ack = value is Map ? _credentialAcks[value['credential_revision']] : null;
        if (ack != null && !ack.isCompleted) ack.complete();
      } on FormatException catch (_) { /* Ordinary stdout is never a credential reply. */ }
    }
  }

  /// Stops the owned child; the installed coordinator never outlives the app.
  @override
  Future<void> stop() async {
    sessionModelCredentials.removeListener(_sendCredentials);
    final process = _process;
    _process = null;
    if (process == null) return;
    try {
      await process.stdin.close().timeout(const Duration(seconds: 2));
    } catch (_) {/* The child may already be gone. */}
    try {
      await process.exitCode.timeout(const Duration(seconds: 6));
    } on TimeoutException {
      process.kill();
      try {
        await process.exitCode.timeout(const Duration(seconds: 3));
      } on TimeoutException {
        process.kill(ProcessSignal.sigkill);
      }
    }
  }
}
