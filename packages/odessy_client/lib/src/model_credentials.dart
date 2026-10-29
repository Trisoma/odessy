import 'models.dart';

/// Safe metadata only. No public response contains a saved API key.
class ModelCredentialStatus {
  ModelCredentialStatus.fromJson(Json value)
      : scope = value['scope'] as String,
        configured = value['configured'] as bool,
        source = value['source'] as String,
        writable = value['writable'] as bool,
        persistence = value['persistence'] as String,
        secureStorageAvailable = value['secure_storage_available'] == true,
        message = value['message'] is String ? value['message'] as String : '' {
    if (scope.isEmpty ||
        scope.length > 128 ||
        !{'env', 'session', 'keyring', 'none'}.contains(source) ||
        !{'persistent', 'session', 'none'}.contains(persistence) ||
        configured != (source != 'none')) {
      throw const FormatException('Invalid credential status.');
    }
  }
  final String scope, source, persistence, message;
  final bool configured, writable, secureStorageAvailable;
}
