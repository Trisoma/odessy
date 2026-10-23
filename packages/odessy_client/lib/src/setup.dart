import 'dart:convert';

import 'models.dart';

Object? _freeze(Object? value) {
  if (value is Map) {
    return Map<String, dynamic>.unmodifiable(
      value.map((key, item) => MapEntry(key as String, _freeze(item))),
    );
  }
  if (value is List) return List<Object?>.unmodifiable(value.map(_freeze));
  return value;
}

/// Local setup protocol. No credentials, arbitrary commands or remote authority.
class SetupRequest {
  SetupRequest({
    required this.id,
    required this.operation,
    required Json selection,
    this.output = '',
    this.format = 'selection',
    this.confirmPlan = '',
    this.endpoint = '',
    Json parameters = const {},
  })  : parameters = _freeze(jsonDecode(jsonEncode(parameters))) as Json,
        selection = _freeze(jsonDecode(jsonEncode(selection))) as Json {
    if (!RegExp(r'^[a-zA-Z0-9_-]{1,80}$').hasMatch(id) ||
        !{
          'review',
          'discover',
          'verify',
          'save',
          'recover',
          'manage',
          'pair',
        }.contains(operation) ||
        !{'selection', 'env'}.contains(format)) {
      throw const FormatException('Invalid setup request.');
    }
  }
  final String id, operation, output, format, confirmPlan, endpoint;
  final Json selection, parameters;
  Json toJson() => {
        'id': id,
        'operation': operation,
        'selection': selection,
        if (parameters.isNotEmpty) 'parameters': parameters,
        if (output.isNotEmpty) 'output': output,
        'format': format,
        if (confirmPlan.isNotEmpty) 'confirm_plan': confirmPlan,
        if (endpoint.isNotEmpty) 'endpoint': endpoint,
      };
}

class SetupReply {
  SetupReply.fromJson(Json value, String expectedId) {
    if (value['id'] != expectedId ||
        !{'ok', 'error'}.contains(value['status'])) {
      throw const FormatException('Invalid or stale setup response.');
    }
    ok = value['status'] == 'ok';
    if (ok && value['result'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid setup result.');
    }
    result = _freeze(value['result'] ?? <String, dynamic>{}) as Json;
    // Do not surface raw subprocess errors or untrusted payloads.
    error = ok
        ? ''
        : 'Setup failed. Check selection, destination and review confirmation.';
  }
  late final bool ok;
  late final Json result;
  late final String error;
}

abstract interface class SetupBackend {
  Future<SetupReply> execute(SetupRequest request);
  Future<void> cancel();
  Future<void> close();
}

class SetupCancelled implements Exception {
  const SetupCancelled();
  @override
  String toString() =>
      'Setup operation cancelled. Inspect the destination before retrying a save.';
}
