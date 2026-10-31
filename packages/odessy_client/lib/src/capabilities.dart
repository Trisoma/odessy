import 'models.dart';

/// Optional additive transport. Older/custom clients need not implement it.
abstract interface class RuntimeConfigurationApi {
  Future<RuntimeCapabilities> runtimeCapabilities();
  Future<String> prepareRestart(String serviceInstance);
  Future<void> resumeAdmission(String ticket);
}

class RuntimeConfiguration {
  RuntimeConfiguration.fromJson(Json value) {
    final fields = value['fingerprints'];
    if (value['owner'] is! String ||
        value['fingerprint'] is! String ||
        fields is! Map ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(value['owner'] as String) ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(value['fingerprint'] as String)) {
      throw const FormatException('Invalid configuration descriptor');
    }
    owner = value['owner'] as String;
    fingerprint = value['fingerprint'] as String;
    fingerprints = Map.unmodifiable({
      for (final key in ['llm', 'stt', 'tts', 'voice', 'host'])
        key: _hash(fields[key])
    });
    final model = value['model'];
    modelName =
        model is Map && model['name'] is String ? model['name'] as String : '';
    provider = model is Map && model['provider'] is String
        ? model['provider'] as String
        : '';
    workspace =
        value['workspace'] is String ? value['workspace'] as String : '';
  }
  static String _hash(Object? value) {
    if (value is! String || !RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
      throw const FormatException('Invalid section fingerprint');
    }
    return value;
  }

  late final String owner, fingerprint, modelName, provider, workspace;
  late final Map<String, String> fingerprints;
}

class RuntimeCapabilities {
  RuntimeCapabilities.fromJson(Json value) {
    if (value['schema_version'] != 1 ||
        value['service_instance_id'] is! String ||
        !RegExp(r'^[0-9a-f]{32}$')
            .hasMatch(value['service_instance_id'] as String) ||
        value['restart_safe'] is! bool)
      throw const FormatException('Invalid runtime capabilities');
    serviceInstance = value['service_instance_id'] as String;
    restartSafe = value['restart_safe'] as bool;
    configuration = value['configuration'] == null
        ? null
        : RuntimeConfiguration.fromJson(value['configuration'] as Json);
  }
  late final String serviceInstance;
  late final bool restartSafe;
  late final RuntimeConfiguration? configuration;
}
