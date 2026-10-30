import 'dart:io';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';

/// Windows Credential Manager; there is no plaintext fallback.
class WindowsCredentialStore implements CredentialStore {
  WindowsCredentialStore({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('odessy/credential_vault');
  final MethodChannel _channel;
  @override
  bool get persistsAcrossRestarts => true;

  String _origin(String value) {
    final uri = Uri.parse(value);
    if (!['http', 'https'].contains(uri.scheme) ||
        !['localhost', '127.0.0.1', '::1'].contains(uri.host) ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/') ||
        uri.port < 1 ||
        uri.port > 65535) {
      throw const FormatException('Invalid credential origin');
    }
    return uri.origin;
  }

  Future<T?> _call<T>(String method, String identity, [String? token]) {
    if (!Platform.isWindows) {
      throw UnsupportedError('Windows vault is unavailable');
    }
    return _channel.invokeMethod<T>(method, {
      'origin': _origin(identity),
      if (token != null) 'token': token,
    });
  }

  @override
  Future<String?> read(String identity) => _call<String>('read', identity);
  @override
  Future<void> write(String identity, String token) async {
    if (token.isEmpty) throw ArgumentError('Empty credential');
    await _call<void>('write', identity, token);
  }

  @override
  Future<void> remove(String identity) async => _call<void>('remove', identity);
}
