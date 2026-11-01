import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  const metadata = <String, dynamic>{
    'scope': 'opaque-scope',
    'configured': true,
    'source': 'keyring',
    'writable': true,
    'persistence': 'persistent',
    'secure_storage_available': true,
    'message': ''
  };
  test(
      'credential status exposes metadata and setup writes remain explicitly local',
      () {
    final status = ModelCredentialStatus.fromJson(metadata);
    expect(status.source, 'keyring');
    expect(status.persistence, 'persistent');
    final request = SetupRequest(
        id: 'credential',
        operation: 'model-credential',
        selection: const {},
        parameters: const {'action': 'describe', 'provider': 'deepseek'});
    expect(request.toJson()['parameters'],
        {'action': 'describe', 'provider': 'deepseek'});
  });
  test('unknown credential sources and inconsistent presence fail closed', () {
    expect(
        () => ModelCredentialStatus.fromJson(
            {...metadata, 'source': 'plaintext'}),
        throwsFormatException);
    expect(
        () =>
            ModelCredentialStatus.fromJson({...metadata, 'configured': false}),
        throwsFormatException);
    expect(
        () => ModelCredentialStatus.fromJson(
            {...metadata, 'persistence': 'forever'}),
        throwsFormatException);
  });
}
