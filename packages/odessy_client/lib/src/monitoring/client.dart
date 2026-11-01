import 'dart:async';
import 'dart:convert';
import 'dart:io';
import '../models.dart';

class MonitoringHost {
  MonitoringHost._(this.id, this.name, this.trust, this.connectivity,
      this.access, this.sample);
  final String id, name, trust, connectivity, access;
  final Json? sample;
  factory MonitoringHost.fromJson(Json value) {
    final id = value['host_id'];
    final trust = (value['trust'] as Map?)?['state'];
    final connectivity = (value['connectivity'] as Map?)?['state'];
    final access = value['monitoring_access'];
    if (id is! String ||
        !RegExp(r'^host_[A-Za-z0-9_]{1,128}$').hasMatch(id) ||
        !['local', 'paired', 'revoked', 'unknown'].contains(trust) ||
        !['unknown', 'online', 'stale', 'offline'].contains(connectivity) ||
        !['allowed', 'denied', 'unsupported', 'disabled'].contains(access)) {
      throw const FormatException('Invalid monitoring host');
    }
    return MonitoringHost._(
        id,
        value['display_name'] as String? ?? id,
        trust as String,
        connectivity as String,
        access as String,
        value['sample'] == null
            ? null
            : freezeJson(Map<String, dynamic>.from(value['sample'] as Map)));
  }
}

class MonitoringSnapshot {
  MonitoringSnapshot(this.hosts, this.cursor, this.nextCursor);
  final List<MonitoringHost> hosts;
  final String cursor;
  final String? nextCursor;
}

class MonitoringEvent {
  MonitoringEvent(this.id, this.type, Json data) : data = freezeJson(data);
  final String? id;
  final String type;
  final Json data;
  bool get requiresResync => type == 'resync_required';
}

class MonitoringFailure implements Exception {
  const MonitoringFailure(this.status);
  final int status;
  @override
  String toString() => 'Monitoring unavailable ($status)';
}

/// Read-only transport; no trust registry, model execution or policy decisions.
class MonitoringClient {
  MonitoringClient(this.base, this._token) {
    if (!['http', 'https'].contains(base.scheme) ||
        base.host.isEmpty ||
        base.userInfo.isNotEmpty ||
        base.hasQuery ||
        base.hasFragment ||
        (base.path.isNotEmpty && base.path != '/') ||
        _token.isEmpty) {
      throw const FormatException('Invalid monitoring origin');
    }
    final loopback = ['127.0.0.1', 'localhost', '::1'].contains(base.host);
    if (base.scheme == 'http' && !loopback) {
      throw const FormatException('Remote monitoring requires HTTPS');
    }
  }
  final Uri base;
  final String _token;
  final HttpClient _http = HttpClient();
  final Set<HttpClient> _streams = {};
  bool _closed = false;
  static const _limit = 2 * 1024 * 1024;

  Future<HttpClientResponse> _open(String path,
      {Map<String, dynamic>? query, String? cursor, HttpClient? client}) async {
    if (_closed) throw StateError('Monitoring client is closed.');
    final uri = base.resolve(path).replace(queryParameters: query);
    if (uri.origin != base.origin)
      throw const FormatException('Origin changed');
    final request =
        await (client ?? _http).getUrl(uri).timeout(const Duration(seconds: 8));
    request.followRedirects = false;
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_token');
    if (cursor != null) {
      if (!RegExp(r'^[A-Za-z0-9_-]{1,128}:\d{1,20}$').hasMatch(cursor)) {
        request.abort();
        throw const FormatException('Invalid monitoring cursor');
      }
      request.headers.set('Last-Event-ID', cursor);
    }
    final response = await request.close().timeout(const Duration(seconds: 12));
    if (response.statusCode != 200) {
      request.abort();
      throw MonitoringFailure(response.statusCode);
    }
    return response;
  }

  Future<Json> _get(String path, {Map<String, dynamic>? query}) async {
    final response = await _open(path, query: query);
    final bytes = <int>[];
    await for (final chunk in response.timeout(const Duration(seconds: 12))) {
      if (bytes.length + chunk.length > _limit) {
        throw const FormatException('Monitoring response too large');
      }
      bytes.addAll(chunk);
    }
    final value = jsonDecode(utf8.decode(bytes));
    if (value is! Map<String, dynamic> || value['schema_version'] != 1) {
      throw const FormatException('Invalid monitoring response');
    }
    return freezeJson(value);
  }

  Future<Json> getOverview() => _get('/v1/monitoring');

  Future<MonitoringSnapshot> listHosts({String? cursor, int limit = 20}) async {
    if (limit < 1 || limit > 32)
      throw const FormatException('Invalid page size');
    final value = await _get('/v1/monitoring/hosts',
        query: {'limit': '$limit', if (cursor != null) 'cursor': cursor});
    final hosts = value['hosts'];
    if (hosts is! List ||
        hosts.length > 32 ||
        value['event_cursor'] is! String) {
      throw const FormatException('Invalid monitoring snapshot');
    }
    return MonitoringSnapshot(
        List.unmodifiable(hosts.map((h) =>
            MonitoringHost.fromJson(Map<String, dynamic>.from(h as Map)))),
        value['event_cursor'] as String,
        value['next_cursor'] as String?);
  }

  String _host(String id) {
    if (!RegExp(r'^host_[A-Za-z0-9_]{1,128}$').hasMatch(id)) {
      throw const FormatException('Invalid host ID');
    }
    return '/v1/monitoring/hosts/$id';
  }

  Future<MonitoringHost> getHost(String id) async => MonitoringHost.fromJson(
      Map<String, dynamic>.from((await _get(_host(id)))["host"] as Map));
  Future<Json> getMetrics(String id) => _get('${_host(id)}/metrics');
  Future<Json> getHistory(String id,
      {required DateTime from,
      required DateTime to,
      int step = 60,
      String series = 'cpu.utilization_percent'}) {
    final names = series.split(',');
    if (!from.isBefore(to) ||
        step < 1 ||
        series.length > 512 ||
        names.length > 16 ||
        names.toSet().length != names.length ||
        names.any((name) => !RegExp(r'^[A-Za-z0-9_.]+$').hasMatch(name)) ||
        !RegExp(r'^[A-Za-z0-9_.,]+$').hasMatch(series)) {
      throw const FormatException('Invalid history query');
    }
    return _get('${_host(id)}/history', query: {
      'from': from.toUtc().toIso8601String(),
      'to': to.toUtc().toIso8601String(),
      'step': '$step',
      'series': names
    });
  }

  Stream<MonitoringEvent> watchEvents({String? after}) {
    HttpClient? client;
    StreamSubscription<MonitoringEvent>? subscription;
    late final StreamController<MonitoringEvent> output;
    output = StreamController<MonitoringEvent>(
        onListen: () {
          if (_closed) {
            output.addError(StateError('Monitoring client is closed.'));
            unawaited(output.close());
            return;
          }
          client = HttpClient();
          _streams.add(client!);
          subscription = _readEvents(client!, after).listen(output.add,
              onError: output.addError,
              onDone: () => unawaited(output.close()));
        },
        onPause: () => subscription?.pause(),
        onResume: () => subscription?.resume(),
        onCancel: () async {
          client?.close(force: true);
          await subscription?.cancel();
        });
    return output.stream;
  }

  Stream<MonitoringEvent> _readEvents(HttpClient client, String? after) async* {
    try {
      final response =
          await _open('/v1/monitoring/events', cursor: after, client: client);
      var buffer = '';
      await for (final chunk in response.transform(utf8.decoder)) {
        buffer += chunk;
        if (buffer.length > _limit)
          throw const FormatException('Monitoring event too large');
        while (true) {
          final boundary = RegExp(r'\r?\n\r?\n').firstMatch(buffer);
          if (boundary == null) break;
          final lines =
              buffer.substring(0, boundary.start).split(RegExp(r'\r?\n'));
          buffer = buffer.substring(boundary.end);
          String? id;
          var type = 'message';
          final data = <String>[];
          for (final line in lines) {
            if (line.startsWith('id:')) id = line.substring(3).trim();
            if (line.startsWith('event:')) type = line.substring(6).trim();
            if (line.startsWith('data:'))
              data.add(line.substring(5).trimLeft());
          }
          if (data.isEmpty) continue;
          final value = jsonDecode(data.join('\n'));
          if (value is! Map<String, dynamic> ||
              value['schema_version'] != 1 ||
              (id != null &&
                  !RegExp(r'^[A-Za-z0-9_-]{1,128}:\d{1,20}$').hasMatch(id))) {
            throw const FormatException('Invalid monitoring event');
          }
          yield MonitoringEvent(id, type, value);
        }
      }
      if (buffer.trim().isNotEmpty)
        throw const FormatException('Truncated monitoring stream');
    } catch (_) {
      // Closing an owned transport while headers are pending is cancellation,
      // not a new service failure. Otherwise preserve the real stream error.
      if (!_closed) rethrow;
    } finally {
      _streams.remove(client);
      client.close(force: true);
    }
  }

  void close() {
    if (_closed) return;
    _closed = true;
    for (final client in _streams.toList()) {
      client.close(force: true);
    }
    _streams.clear();
    _http.close(force: true);
  }
}
