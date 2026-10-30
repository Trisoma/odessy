library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'src/models.dart';
import 'src/monitoring/client.dart';
export 'src/models.dart';
export 'src/credentials.dart';
export 'src/setup.dart';
export 'src/monitoring/client.dart';

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

/// Session-scoped jobs; submission is authorized by the service approval broker.
abstract interface class JobsApi {
  Future<Json> listJobs(String sid, {String? after, int limit = 50});
  Future<Json> readJob(String sid, String jobId);
  Future<Json> submitJob(String sid, Json request);
  Future<Json> cancelJob(String sid, String jobId);
}

/// Redacted runtime inventory; it does not expose values or mutate settings.
abstract interface class RuntimeSettingsApi {
  Future<Json> runtimeSettings();
}

/// Compare-and-save for the connected service's collaboration limit.
abstract interface class ConsultationLimitApi {
  Future<Json> readConsultationLimit();
  Future<Json> saveConsultationLimit(Json update);
}

abstract interface class ContextLimitApi {
  Future<Json> readContextLimit();
  Future<Json> saveContextLimit(Json update);
}

abstract interface class RoundLimitApi {
  Future<Json> readRoundLimit();
  Future<Json> saveRoundLimit(Json update);
}

abstract interface class CollaborationEnabledApi {
  Future<Json> readCollaborationEnabled();
  Future<Json> saveCollaborationEnabled(Json update);
}

abstract interface class ModelProfilesApi {
  Future<Json> modelProfiles();
  Future<Json> updateModelProfile(String name, Json update);
}

abstract interface class ModelProfileManagementApi {
  Future<List<Json>> modelProfileProviders();
  Future<Json> createModelProfile(Json value);
  Future<Json> deleteModelProfile(String name, Json version);
}

abstract interface class ModelProviderSwitchApi {
  Future<Json> switchModelProvider(String name, Json update);
}

abstract interface class ModelProfilePricingApi {
  Future<Json> modelProfilePricing(String name);
  Future<Json> saveModelProfilePricing(String name, Json update);
}

abstract interface class ModelCredentialReferenceApi {
  Future<Json> modelCredentialReference(String name);
  Future<Json> saveModelCredentialReference(String name, Json update);
}

abstract interface class ModelProfileLimitsApi {
  Future<Json> modelProfileLimits(String name);
  Future<Json> saveModelProfileLimits(String name, Json update);
}

abstract interface class VoiceApi {
  Future<Json> voiceStatus(String sid);
  Future<Json> startVoice(String sid);
  Future<Json> heartbeatVoice(String sid);
  Future<Json> stopVoice(String sid);
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

class LocalApi
    implements
        OdessyApi,
        ProfileMetadataApi,
        JobsApi,
        RuntimeSettingsApi,
        ConsultationLimitApi,
        ContextLimitApi,
        RoundLimitApi,
        CollaborationEnabledApi,
        ModelProfilesApi,
        ModelProfileManagementApi,
        ModelProviderSwitchApi,
        ModelProfilePricingApi,
        ModelCredentialReferenceApi,
        ModelProfileLimitsApi,
        VoiceApi {
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
  final Uri base;
  final String _token;
  final HttpClient _http = HttpClient()..autoUncompress = true;
  final Set<HttpClient> _streams = {};
  final Set<MonitoringClient> _monitoring = {};
  bool _closed = false;

  /// Read-only monitoring bound to this immutable origin and in-memory bearer.
  MonitoringClient createMonitoringClient() {
    if (_closed) throw StateError('Local client is closed.');
    late final MonitoringClient client;
    client =
        _OwnedMonitoringClient(base, _token, () => _monitoring.remove(client));
    _monitoring.add(client);
    return client;
  }

  @override
  bool get isDemo => false;
  @override
  String get identity => base.origin;
  Future<HttpClientResponse> _open(String path, String method,
      [Json? data,
      HttpClient? client,
      Duration responseTimeout = const Duration(seconds: 12)]) async {
    if (_closed) throw StateError('Local client is closed.');
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
    final HttpClientResponse response;
    try {
      response = await request.close().timeout(responseTimeout);
    } on TimeoutException {
      request.abort();
      rethrow;
    }
    if (response.statusCode >= 300) {
      await response.drain<void>().timeout(const Duration(seconds: 12));
      throw ApiFailure(response.statusCode);
    }
    return response;
  }

  Future<Json> _json(String path,
      [String method = 'GET',
      Json? data,
      Duration responseTimeout = const Duration(seconds: 12)]) async {
    final response = await _open(path, method, data, null, responseTimeout);
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

  Future<Json> _voice(String sid, [String action = '']) async {
    final response = await _json(
        '${_session(sid)}/voice$action', action.isEmpty ? 'GET' : 'POST');
    if (!const {
          'off',
          'starting',
          'active',
          'stopping',
          'unavailable',
          'stop_failed'
        }.contains(response['state']) ||
        response['input_available'] is! bool ||
        response['output_available'] is! bool ||
        response.keys.any((key) => !const {
              'state',
              'input_available',
              'output_available'
            }.contains(key))) {
      throw const FormatException('Invalid voice state');
    }
    return freezeJson(response);
  }

  @override
  Future<Json> voiceStatus(String sid) => _voice(sid);
  @override
  Future<Json> startVoice(String sid) => _voice(sid, '/start');
  @override
  Future<Json> heartbeatVoice(String sid) => _voice(sid, '/heartbeat');
  @override
  Future<Json> stopVoice(String sid) => _voice(sid, '/stop');

  @override
  Future<Json> runtimeSettings() async {
    final result = await _json('/v1/runtime-settings');
    if (result.keys.any((key) => !const {
              'schema_version',
              'instance_id',
              'settings'
            }.contains(key)) ||
        result['schema_version'] != 1 ||
        result['instance_id'] is! String ||
        (result['instance_id'] as String).isEmpty ||
        result['settings'] is! List ||
        (result['settings'] as List).length > 512) {
      throw const FormatException('Invalid runtime settings');
    }
    final names = <String>{};
    for (final item in result['settings'] as List) {
      if (item is! Map ||
          item['name'] is! String ||
          !RegExp(r'^ODESSY_[A-Z0-9_]{1,128}$')
              .hasMatch(item['name'] as String) ||
          !names.add(item['name'] as String) ||
          item['kind'] is! String ||
          item['summary'] is! String ||
          item['configured'] is! bool ||
          item['sensitive'] is! bool ||
          item['internal'] is! bool ||
          item['editable'] != false ||
          item.keys.any((key) => !const {
                'name',
                'kind',
                'summary',
                'configured',
                'sensitive',
                'internal',
                'editable'
              }.contains(key))) {
        throw const FormatException('Invalid runtime setting');
      }
    }
    return freezeJson(result);
  }

  String _job(String sid, String id) {
    if (!RegExp(r'^[a-f0-9]{32}$').hasMatch(id)) {
      throw const FormatException('Invalid job ID');
    }
    return '${_session(sid)}/jobs/$id';
  }

  @override
  Future<Json> listJobs(String sid, {String? after, int limit = 50}) async {
    if (limit < 1 ||
        limit > 50 ||
        (after != null && !RegExp(r'^[a-f0-9]{32}$').hasMatch(after))) {
      throw const FormatException('Invalid job page');
    }
    final query = Uri(queryParameters: {
      'limit': '$limit',
      if (after != null) 'after': after,
    }).query;
    final result = await _json('${_session(sid)}/jobs?$query');
    if (result['jobs'] is! List ||
        (result['jobs'] as List).length > limit ||
        (result['next_cursor'] != null &&
            (result['next_cursor'] is! String ||
                !RegExp(r'^[a-f0-9]{32}$')
                    .hasMatch(result['next_cursor'] as String)))) {
      throw const FormatException('Invalid job listing');
    }
    for (final item in result['jobs'] as List) {
      if (item is! Map ||
          item['job_id'] is! String ||
          !RegExp(r'^[a-f0-9]{32}$').hasMatch(item['job_id'] as String) ||
          item['state'] is! String ||
          item['participant'] is! String ||
          item['mode'] is! String ||
          item['remote_state_unknown'] is! bool) {
        throw const FormatException('Invalid job summary');
      }
    }
    return freezeJson(result);
  }

  @override
  Future<Json> readJob(String sid, String jobId) => _json(_job(sid, jobId));
  @override
  Future<Json> submitJob(String sid, Json request) => _json(
      '${_session(sid)}/jobs', 'POST', request, const Duration(minutes: 5));
  @override
  Future<Json> cancelJob(String sid, String jobId) =>
      _json('${_job(sid, jobId)}/cancel', 'POST');
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

  Json _validConsultationLimit(Json value) {
    if (value.keys.toSet().difference(
            {'instance_id', 'revision', 'max_consultations'}).isNotEmpty ||
        value.length != 3 ||
        value['instance_id'] is! String ||
        (value['instance_id'] as String).isEmpty ||
        value['revision'] is! int ||
        (value['revision'] as int) < 0 ||
        value['max_consultations'] is! int ||
        (value['max_consultations'] as int) < 0 ||
        (value['max_consultations'] as int) > 32) {
      throw const FormatException('Invalid collaboration limit');
    }
    return value;
  }

  @override
  Future<Json> readConsultationLimit() async =>
      _validConsultationLimit(await _json('/v1/settings/consultation-limit'));

  @override
  Future<Json> saveConsultationLimit(Json update) async =>
      _validConsultationLimit(
          await _json('/v1/settings/consultation-limit', 'PUT', update));

  Json _validContextLimit(Json value) {
    if (value.length != 3 ||
        value['instance_id'] is! String ||
        (value['instance_id'] as String).isEmpty ||
        value['revision'] is! int ||
        (value['revision'] as int) < 0 ||
        value['max_context_chars'] is! int ||
        (value['max_context_chars'] as int) < 1 ||
        (value['max_context_chars'] as int) > 100000) {
      throw const FormatException('Invalid context limit');
    }
    return freezeJson(value);
  }

  @override
  Future<Json> readContextLimit() async =>
      _validContextLimit(await _json('/v1/settings/context-limit'));
  @override
  Future<Json> saveContextLimit(Json update) async => _validContextLimit(
      await _json('/v1/settings/context-limit', 'PUT', update));

  Json _validRoundLimit(Json value) {
    if (value.length != 3 ||
        value['instance_id'] is! String ||
        (value['instance_id'] as String).isEmpty ||
        value['revision'] is! int ||
        (value['revision'] as int) < 0 ||
        value['max_rounds'] is! int ||
        (value['max_rounds'] as int) < 0 ||
        (value['max_rounds'] as int) > 32) {
      throw const FormatException('Invalid round limit');
    }
    return freezeJson(value);
  }

  @override
  Future<Json> readRoundLimit() async =>
      _validRoundLimit(await _json('/v1/settings/round-limit'));
  @override
  Future<Json> saveRoundLimit(Json update) async =>
      _validRoundLimit(await _json('/v1/settings/round-limit', 'PUT', update));

  Json _validCollaborationEnabled(Json value) {
    if (value.length != 3 ||
        value['instance_id'] is! String ||
        (value['instance_id'] as String).isEmpty ||
        value['revision'] is! int ||
        (value['revision'] as int) < 0 ||
        value['enabled'] is! bool) {
      throw const FormatException('Invalid collaboration setting');
    }
    return freezeJson(value);
  }

  @override
  Future<Json> readCollaborationEnabled() async => _validCollaborationEnabled(
      await _json('/v1/settings/collaboration-enabled'));
  @override
  Future<Json> saveCollaborationEnabled(Json update) async =>
      _validCollaborationEnabled(
          await _json('/v1/settings/collaboration-enabled', 'PUT', update));

  Json _validModelProfiles(Json data) {
    if (data.length != 3 ||
        data['instance_id'] is! String ||
        (data['instance_id'] as String).isEmpty ||
        data['revision'] is! int ||
        (data['revision'] as int) < 0 ||
        data['profiles'] is! List ||
        (data['profiles'] as List).length > 128) {
      throw const FormatException('Invalid model profiles');
    }
    final names = <String>{};
    for (final item in data['profiles'] as List) {
      if (item is! Map ||
          item.length != 5 ||
          item['name'] is! String ||
          !RegExp(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
              .hasMatch(item['name'] as String) ||
          !names.add(item['name'] as String) ||
          !const {'cloud', 'ollama'}.contains(item['adapter']) ||
          item['provider'] is! String ||
          item['model'] is! String ||
          item['endpoint'] is! String ||
          (item['provider'] as String).length > 4096 ||
          (item['model'] as String).length > 4096 ||
          (item['endpoint'] as String).length > 4096) {
        throw const FormatException('Invalid model profile');
      }
    }
    return freezeJson(data);
  }

  @override
  Future<Json> modelProfiles() async =>
      _validModelProfiles(await _json('/v1/settings/model-profiles'));

  @override
  Future<Json> updateModelProfile(String name, Json update) async =>
      _validModelProfiles(await _json(
          '/v1/settings/model-profiles/${Uri.encodeComponent(name)}',
          'PUT',
          update));

  @override
  Future<Json> modelProfileLimits(String name) async {
    final result = await _json(
        '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/limits');
    if (result.length != 4 ||
        result['instance_id'] is! String ||
        (result['instance_id'] as String).isEmpty ||
        result['revision'] is! int ||
        (result['revision'] as int) < 0 ||
        result['timeout'] is! int ||
        (result['timeout'] as int) < 1 ||
        (result['timeout'] as int) > 3600 ||
        result['max_tokens'] is! int ||
        (result['max_tokens'] as int) < 1 ||
        (result['max_tokens'] as int) > 1000000) {
      throw const FormatException('Invalid model limits');
    }
    return freezeJson(result);
  }

  @override
  Future<Json> saveModelProfileLimits(String name, Json update) async =>
      _validModelProfiles(await _json(
          '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/limits',
          'PUT',
          update));

  @override
  Future<List<Json>> modelProfileProviders() async {
    final data = await _json('/v1/settings/model-profile-providers');
    if (data.length != 1 ||
        data['providers'] is! List ||
        (data['providers'] as List).length > 128) {
      throw const FormatException('Invalid provider catalog');
    }
    final names = <String>{};
    final result = <Json>[];
    for (final item in data['providers'] as List) {
      if (item is! Map ||
          item.length != 2 ||
          item['name'] is! String ||
          !RegExp(r'^[a-z][a-z0-9_]{0,63}$').hasMatch(item['name'] as String) ||
          !names.add(item['name'] as String) ||
          item['credential_env'] is! String ||
          !RegExp(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
              .hasMatch(item['credential_env'] as String)) {
        throw const FormatException('Invalid provider catalog');
      }
      result.add(freezeJson(Map<String, dynamic>.from(item)));
    }
    return List.unmodifiable(result);
  }

  @override
  Future<Json> createModelProfile(Json value) async => _validModelProfiles(
      await _json('/v1/settings/model-profiles', 'POST', value));

  @override
  Future<Json> deleteModelProfile(String name, Json version) async =>
      _validModelProfiles(await _json(
          '/v1/settings/model-profiles/${Uri.encodeComponent(name)}',
          'DELETE',
          version));

  @override
  Future<Json> switchModelProvider(String name, Json update) async =>
      _validModelProfiles(await _json(
          '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/provider',
          'PUT',
          update));

  @override
  Future<Json> modelProfilePricing(String name) async {
    final data = await _json(
        '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/pricing');
    if (data.length != 3 ||
        data['instance_id'] is! String ||
        (data['instance_id'] as String).isEmpty ||
        data['revision'] is! int ||
        (data['revision'] as int) < 0 ||
        !data.containsKey('pricing')) {
      throw const FormatException('Invalid pricing');
    }
    final pricing = data['pricing'];
    if (pricing != null &&
        (pricing is! Map ||
            pricing.length != 4 ||
            pricing['currency'] is! String ||
            (pricing['currency'] as String).length > 32 ||
            ['input', 'cached_input', 'output'].any((key) =>
                pricing[key] is! num ||
                !(pricing[key] as num).isFinite ||
                (pricing[key] as num) < 0))) {
      throw const FormatException('Invalid pricing');
    }
    return freezeJson(data);
  }

  @override
  Future<Json> saveModelProfilePricing(String name, Json update) async =>
      _validModelProfiles(await _json(
          '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/pricing',
          'PUT',
          update));

  @override
  Future<Json> modelCredentialReference(String name) async {
    final data = await _json(
        '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/credential-reference');
    if (data.length != 4 ||
        data['instance_id'] is! String ||
        (data['instance_id'] as String).isEmpty ||
        data['revision'] is! int ||
        (data['revision'] as int) < 0 ||
        data['credential_env'] is! String ||
        (data['credential_env'] as String).length > 4096 ||
        data['default_credential_env'] is! String ||
        (data['default_credential_env'] as String).length > 128) {
      throw const FormatException('Invalid credential reference');
    }
    return freezeJson(data);
  }

  @override
  Future<Json> saveModelCredentialReference(String name, Json update) async =>
      _validModelProfiles(await _json(
          '/v1/settings/model-profiles/${Uri.encodeComponent(name)}/credential-reference',
          'PUT',
          update));
  @override
  Future<void> deleteSession(String sid) async {
    await _json(_session(sid), 'DELETE');
  }

  @override
  void close() {
    if (_closed) return;
    _closed = true;
    for (final client in _monitoring.toList()) {
      client.close();
    }
    _monitoring.clear();
    for (final client in _streams.toList()) {
      client.close(force: true);
    }
    _streams.clear();
    _http.close(force: true);
  }
}

class _OwnedMonitoringClient extends MonitoringClient {
  _OwnedMonitoringClient(super.base, super.token, this._onClose);
  final void Function() _onClose;
  bool _notified = false;
  @override
  void close() {
    if (_notified) return;
    _notified = true;
    try {
      super.close();
    } finally {
      _onClose();
    }
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
  Future<List<String>> profiles() async => ['auto', 'local', 'cloud-example'];
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
            'This is a demonstration response. No model or tool ran. Connect a local Odessy service for a real conversation.');
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
