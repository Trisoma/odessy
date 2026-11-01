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

/// Local setup protocol. Credential writes are private stdin-only operations.
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
          'launch',
          'activate',
          'resume',
          'voice-discover',
          'model-catalog',
          'component-settings',
          'component-probe',
          'model-credential',
          'manage',
          'installer',
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
    reason = ok ? '' : _boundedReason(value['reason']);
    code = value['code'] is String &&
            RegExp(r'^[a-z][a-z0-9_]{0,63}$').hasMatch(value['code'] as String)
        ? value['code'] as String
        : null;
    fieldErrors=Map.unmodifiable(value['field_errors'] is Map ? {
      for(final entry in (value['field_errors'] as Map).entries)
        if(entry.key is String && entry.value is String && (entry.key as String).length<=64)
          entry.key as String: _boundedReason(entry.value)
    } : <String,String>{});
  }
  late final bool ok;
  late final Json result;
  late final String error;

  /// A bounded, single-line helper reason; never raw stdout or a command line.
  late final String reason;
  /// Additive safe error category. Unknown categories remain compatible.
  late final String? code;
  late final Map<String,String> fieldErrors;

  static String _boundedReason(Object? value) {
    if (value is! String) return '';
    final cleaned = value.replaceAll(RegExp(r'[\x00-\x1f\x7f]'), ' ').trim();
    return cleaned.length > 300 ? cleaned.substring(0, 300) : cleaned;
  }
}

abstract interface class SetupBackend {
  Future<SetupReply> execute(SetupRequest request);
  Future<void> cancel();
  Future<void> close();
}

/// Separate read-only metadata helpers from a foreground setup transaction.
abstract interface class SetupMetadataBackend {
  SetupBackend createMetadataReader();
}

class SetupCancelled implements Exception {
  const SetupCancelled();
  @override
  String toString() =>
      'Setup operation cancelled. Inspect the destination before retrying a save.';
}
