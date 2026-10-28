library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'src/models.dart';
export 'src/models.dart';
export 'src/credentials.dart';
export 'src/setup.dart';
export 'src/monitoring/client.dart';
export 'src/capabilities.dart';
import 'src/capabilities.dart';
export 'src/voice.dart';
import 'src/voice.dart';
import 'src/monitoring/client.dart';

/// Bounded SSE parsing. UTF-8 decoding belongs to the transport, before this parser.
Stream<SessionEvent> decodeEvents(Stream<String> chunks) async* {
  var buffer = '', lastId = -1;
  await for (final chunk in chunks) {
    buffer += chunk;
    if (buffer.length > 2 * 1024 * 1024)
      throw const FormatException('Event frame exceeds 2 MiB');
    while (true) {
      final boundary = RegExp(r'\r?\n\r?\n').firstMatch(buffer);
      if (boundary == null) break;
      final block = buffer.substring(0, boundary.start);
      buffer = buffer.substring(boundary.end);
      final data = block
          .split(RegExp(r'\r?\n'))
          .where((l) => l.startsWith('data:'))
          .map((l) => l.substring(5).trimLeft())
          .join('\n');
      if (data.isEmpty) continue;
      final event = SessionEvent.fromJson(jsonDecode(data) as Json);
      if (event.id <= lastId) continue;
      lastId = event.id;
      yield event;
    }
  }
}

abstract class OdessyApi {
  bool get isDemo;
  String get identity;
  Future<List<String>> profiles();
  Future<Session> createSession();
  Future<Session> snapshot(String sid);
  Stream<SessionEvent> events(String sid, int after);
  Future<void> send(String sid, String text);
  Future<void> select(String sid, String profile);
  Future<void> stop(String sid);
  Future<void> approve(String sid, String id, bool approved);
  Future<void> answer(String sid, String id, String answer);
  Future<Json> settings();
  Future<Json> saveSettings(Json value);
  Future<void> deleteSession(String sid);
  void close();
}

class ApiFailure implements Exception {
  const ApiFailure(this.status);
  final int status;
  @override
  String toString() => switch (status) {
        401 => 'Authentication failed. Check the bearer token.',
        404 ||
        409 ||
        410 =>
          'Request is stale, already resolved, or unavailable. Refresh the session.',
        _ => 'Odessy service returned HTTP $status.',
      };
}

class LocalApi implements OdessyApi, ProfileMetadataApi, RuntimeConfigurationApi, DesktopVoiceApi {
  LocalApi(this.base, String token) : _token = token {
    if (!['http', 'https'].contains(base.scheme) ||
        !['localhost', '127.0.0.1', '::1'].contains(base.host) ||
        base.userInfo.isNotEmpty ||
        base.hasQuery ||
        base.hasFragment ||
        (base.path.isNotEmpty && base.path != '/') ||
        base.port < 1 ||
        base.port > 65535 ||
        token.trim().isEmpty) {
      throw ArgumentError(
          'Use a loopback HTTP address and a nonempty bearer token.');
    }
    _http.connectionTimeout = const Duration(seconds: 5);
  }

  /// Shares authenticated origin without exposing the credential.
  MonitoringClient createMonitoringClient() => MonitoringClient(base, _token);
  final Uri base;
  final String _token;
  final HttpClient _http = HttpClient()..autoUncompress = true;
  final Set<HttpClient> _streams = {};
  @override
  bool get isDemo => false;
  @override
  String get identity => base.origin;
  Future<HttpClientResponse> _open(String path, String method,
      [Json? data, HttpClient? client]) async {
    final request = await (client ?? _http)
        .openUrl(method, base.resolve(path))
        .timeout(const Duration(seconds: 8));
    request.followRedirects =
        false; // Never forward the token to another origin.
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_token');
    if (data != null) {
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(data));
    }
    final response = await request.close().timeout(const Duration(seconds: 12));
    if (response.statusCode >= 300) {
      await response.drain<void>().timeout(const Duration(seconds: 12));
      throw ApiFailure(response.statusCode);
    }
    return response;
  }

  Future<Json> _json(String path, [String method = 'GET', Json? data]) async {
    final response = await _open(path, method, data);
    if (response.statusCode == 204) return {};
    final bytes = <int>[];
    await for (final chunk in response.timeout(const Duration(seconds: 12))) {
      if (bytes.length + chunk.length > 4 * 1024 * 1024)
        throw const FormatException('Response exceeds 4 MiB');
      bytes.addAll(chunk);
    }
    return jsonDecode(utf8.decode(bytes)) as Json;
  }

  String _session(String sid) => '/v1/sessions/${Uri.encodeComponent(sid)}';
  @override Future<VoiceSnapshot> voiceState(String sid) async => VoiceSnapshot.fromJson(await _json('${_session(sid)}/voice'));
  @override Future<VoiceSnapshot> voiceCommand(String sid,String action,{String? captureId,String? interactionId}) async =>
      VoiceSnapshot.fromJson(await _json('${_session(sid)}/voice','POST',{'action':action,if(captureId!=null)'capture_id':captureId,if(interactionId!=null)'expected_interaction_id':interactionId}));
  @override
  Future<RuntimeCapabilities> runtimeCapabilities() async => RuntimeCapabilities.fromJson(await _json('/v1/capabilities'));
  @override
  Future<String> prepareRestart(String serviceInstance) async =>
      (await _json('/v1/lifecycle/prepare-restart','POST',{'service_instance_id':serviceInstance}))['ticket'] as String;
  @override
  Future<void> resumeAdmission(String ticket) async { await _json('/v1/lifecycle/resume','POST',{'ticket':ticket}); }
  @override
  Future<List<String>> profiles() async =>
      ((await _json('/v1/profiles'))['profiles'] as Map)
          .keys
          .cast<String>()
          .toList();
  @override
  Future<List<Profile>> profileMetadata() async {
    final values = (await _json('/v1/profiles'))['profiles'] as Map;
    return List.unmodifiable(values.entries.map((e) => Profile(e.key as String,
        e.value is Map ? Map<String, dynamic>.from(e.value) : {})));
  }

  @override
  Future<Session> createSession() async =>
      Session.fromJson(await _json('/v1/sessions', 'POST'));
  @override
  Future<Session> snapshot(String sid) async =>
      Session.fromJson(await _json(_session(sid)));
  @override
  Stream<SessionEvent> events(String sid, int after) {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    _streams.add(client);
    var cancelled = false;
    StreamSubscription<SessionEvent>? subscription;
    late final StreamController<SessionEvent> output;
    output = StreamController<SessionEvent>(onListen: () {
      unawaited(() async {
        try {
          final response = await _open(
              '${_session(sid)}/events?after_id=$after', 'GET', null, client);
          if (cancelled) return;
          subscription = decodeEvents(utf8.decoder
                  .bind(response.timeout(const Duration(seconds: 35))))
              .listen((event) {
            if (!cancelled) output.add(event);
          }, onError: (Object error, StackTrace stack) {
            if (!cancelled) output.addError(error, stack);
          }, onDone: () {
            _streams.remove(client);
            client.close(force: true);
            unawaited(output.close());
          });
        } catch (error, stack) {
          if (!cancelled) {
            output.addError(error, stack);
            unawaited(output.close());
          }
        }
      }());
    }, onCancel: () {
      cancelled = true;
      _streams.remove(client);
      // Close this stream's socket before cancelling the async parser, whose
      // pending read otherwise waits for the next heartbeat indefinitely.
      client.close(force: true);
      unawaited(subscription?.cancel().catchError((Object _) {}));
    });
    return output.stream;
  }

  @override
  Future<void> send(String sid, String text) async {
    await _json('${_session(sid)}/turns', 'POST', {'text': text});
  }

  @override
  Future<void> select(String sid, String profile) async {
    await _json('${_session(sid)}/model', 'PUT', {'profile': profile});
  }

  @override
  Future<void> stop(String sid) async {
    await _json('${_session(sid)}/stop', 'POST');
  }

  @override
  Future<void> approve(String sid, String id, bool approved) async {
    await _json('${_session(sid)}/approvals/${Uri.encodeComponent(id)}', 'POST',
        {'approved': approved});
  }

  @override
  Future<void> answer(String sid, String id, String answer) async {
    await _json('${_session(sid)}/questions/${Uri.encodeComponent(id)}', 'POST',
        {'answer': answer});
  }

  @override
  Future<Json> settings() => _json('/v1/settings');
  @override
  Future<Json> saveSettings(Json value) => _json('/v1/settings', 'PUT', value);
  @override
  Future<void> deleteSession(String sid) async {
    await _json(_session(sid), 'DELETE');
  }

  @override
  void close() {
    for (final client in _streams.toList()) {
      client.close(force: true);
    }
    _streams.clear();
    _http.close(force: true);
  }
}

/// Presentation fixtures only: no models, tools or processes execute here.
class DemoApi implements OdessyApi {
  DemoApi({this.delay = const Duration(milliseconds: 1000)});
  final Duration delay;
  final _stream = StreamController<SessionEvent>.broadcast();
  final List<SessionEvent> _events = [];
  final List<ChatMessage> _messages = [];
  final List<Json> _pending = [];
  String _selected = 'auto';
  String? _active;
  bool _running = false, _closed = false;
  int _generation = 0;
  Json _settings = {
    'settings': {
      'enabled': false,
      'automatic': false,
      'max_consultations': 1,
      'max_context_chars': 8000,
      'max_rounds': 1,
      'default_participant': '',
      'rules': []
    },
    'participants': <String, dynamic>{}
  };
  @override
  bool get isDemo => true;
  @override
  String get identity => 'Demo workstation';
  void _emit(String type, Json data) {
    if (_closed) return;
    final event = SessionEvent(_events.length + 1, type, data);
    _events.add(event);
    _stream.add(event);
  }

  @override
  Future<List<String>> profiles() async => ['auto', 'local', 'cloud-preview'];
  @override
  Future<Session> createSession() => snapshot('demo');
  @override
  Future<Session> snapshot(String sid) async => Session('demo',
      selectedProfile: _selected,
      activeProfile: _active,
      running: _running,
      messages: List.unmodifiable(_messages),
      pending: List.unmodifiable(_pending),
      events: List.unmodifiable(_events));
  @override
  Stream<SessionEvent> events(String sid, int after) => _stream.stream;
  @override
  Future<void> send(String sid, String text) async {
    if (_running) throw const ApiFailure(409);
    _running = true;
    _active = _selected;
    final generation = ++_generation;
    _messages.add(ChatMessage('user', text));
    _emit('transcript', {'role': 'user', 'text': text});
    _emit('selection',
        {'profile': _active, 'reason': 'Demo: pinned for this turn'});
    unawaited(Future<void>.delayed(delay, () {
      if (_closed || generation != _generation) return;
      if (text == '/approval' || text == '/question') {
        final approval = text == '/approval';
        final entry = <String, dynamic>{
          'id': 'demo-$generation',
          'kind': approval ? 'approval' : 'question',
          'prompt':
              'Odessy requests permission to run the exact command below.',
          'question': 'Which repository should I inspect?',
          'tool': 'terminal.run',
          'arguments': {'command': 'git status --short'},
          'host': 'Demo workstation',
          'risk': 'medium',
          'allowed_channels': ['authenticated_ui'],
          'choices': ['Odessy', 'Other'],
          'allow_free_text': true,
          'expires_at': DateTime.now().millisecondsSinceEpoch / 1000 + 120
        };
        _pending.add(entry);
        _emit(approval ? 'approval_required' : 'question_required', entry);
      } else {
        _finish(
            'This is a preview response. No model or tool ran. Connect a local Odessy service for a real conversation.');
      }
    }));
  }

  void _finish(String text) {
    _messages.add(ChatMessage('assistant', text));
    _emit('transcript', {'role': 'assistant', 'text': text});
    _running = false;
    _active = null;
    _emit('result', {});
  }

  @override
  Future<void> select(String sid, String profile) async {
    _selected = profile;
    _emit('selection', {'profile': profile, 'reason': 'Applies to next turn'});
  }

  @override
  Future<void> stop(String sid) async {
    _generation++;
    _pending.clear();
    _running = false;
    _active = null;
    _emit('stopped', {});
  }

  void _resolve(String id) {
    if (!_pending.any((e) => e['id'] == id)) throw const ApiFailure(409);
    _pending.removeWhere((e) => e['id'] == id);
  }

  @override
  Future<void> approve(String sid, String id, bool approved) async {
    _resolve(id);
    _emit('approval_answered', {'id': id});
    _finish(
        'Demo action ${approved ? 'approved' : 'denied'}. Nothing was executed.');
  }

  @override
  Future<void> answer(String sid, String id, String answer) async {
    _resolve(id);
    _emit('question_answered', {'id': id});
    _finish('Demo answer received: $answer');
  }

  @override
  Future<Json> settings() async => jsonDecode(jsonEncode(_settings)) as Json;
  @override
  Future<Json> saveSettings(Json value) async {
    final count = (value['settings'] as Map)['max_consultations'];
    if (count is! int || count < 0 || count > 32) throw const ApiFailure(400);
    _settings = jsonDecode(jsonEncode(value)) as Json;
    return settings();
  }

  @override
  Future<void> deleteSession(String sid) => stop(sid);
  @override
  void close() {
    _closed = true;
    _generation++;
    unawaited(_stream.close());
  }
}
