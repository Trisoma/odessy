/// Native durable adapters must explicitly declare their persistence behavior.
abstract interface class CredentialStore {
  bool get persistsAcrossRestarts;
  Future<String?> read(String identity);
  Future<void> write(String identity, String token);
  Future<void> remove(String identity);
}

/// Preview credentials live only in memory, never in preferences or files.
class MemoryCredentialStore implements CredentialStore {
  final Map<String, String> _tokens = {};
  @override
  bool get persistsAcrossRestarts => false;
  @override
  Future<String?> read(String identity) async => _tokens[identity];
  @override
  Future<void> write(String identity, String token) async {
    _tokens[identity] = token;
  }

  @override
  Future<void> remove(String identity) async {
    _tokens.remove(identity);
  }
}
