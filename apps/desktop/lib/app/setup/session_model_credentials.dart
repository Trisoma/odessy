import 'dart:convert';
import 'dart:async';
import 'package:odessy_client/odessy_client.dart';

/// App-owned ephemeral values, delivered only over owned helpers' private stdin.
/// Never expose this object through a session API or persistent preferences.
class SessionModelCredentials {
  final Map<String, Json> _values = {};
  final Set<FutureOr<void> Function()> _listeners = {};
  int revision = 0;
  List<Json> get privateStdinValues =>
      [for (final v in _values.values) Map.of(v)];
  void addListener(FutureOr<void> Function() callback) =>
      _listeners.add(callback);
  void removeListener(FutureOr<void> Function() callback) =>
      _listeners.remove(callback);

  void validate(SetupRequest request) {
    if (request.operation != 'model-credential' ||
        request.parameters['action'] != 'set') {
      return;
    }
    final value = request.parameters['value'];
    if (value is! String ||
        value.trim().isEmpty ||
        utf8.encode(value).length > 16384 ||
        value.contains(RegExp(r'[\r\n\x00]'))) {
      throw const FormatException('Enter a valid API key without line breaks.');
    }
    if (request.parameters['storage'] == 'session' &&
        (_values.length >= 32 ||
            utf8.encode(jsonEncode(privateStdinValues)).length +
                    utf8.encode(value).length >
                48000)) {
      throw const FormatException('Session credential memory limit reached.');
    }
  }

  Future<void> accept(SetupRequest request, SetupReply reply) async {
    if (request.operation != 'model-credential' || !reply.ok) return;
    final action = request.parameters['action'];
    if (action == 'describe') return;
    final scope = reply.result['scope'] as String;
    _values.remove(scope);
    if (action == 'set' && request.parameters['storage'] == 'session') {
      _values[scope] = {
        'provider': request.parameters['provider'],
        'endpoint': request.parameters['endpoint'] ?? '',
        'credential_env': request.parameters['credential_env'] ?? '',
        'value': (request.parameters['value'] as String).trim(),
      };
    }
    revision++;
    await _notify();
  }

  Future<void> clear() async {
    _values.clear();
    revision++;
    await _notify();
  }

  Future<void> _notify() async {
    for (final callback in List.of(_listeners)) {
      await callback();
    }
  }
}

final sessionModelCredentials = SessionModelCredentials();
