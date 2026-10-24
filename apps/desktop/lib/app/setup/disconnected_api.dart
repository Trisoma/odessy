import 'package:odessy_client/odessy_client.dart';

/// No transport, inference or mock responses before a coordinator is configured.
class DisconnectedApi implements OdessyApi {
  @override
  bool get isDemo => false;
  @override
  String get identity => 'Model not configured';
  Never _unavailable() => throw StateError(
      'Model not configured. Open Models and connections to finish setup.');
  @override
  Future<List<String>> profiles() async => _unavailable();
  @override
  Future<Session> createSession() async => _unavailable();
  @override
  Future<Session> snapshot(String sid) async => _unavailable();
  @override
  Stream<SessionEvent> events(String sid, int after) => const Stream.empty();
  @override
  Future<void> send(String sid, String text) async => _unavailable();
  @override
  Future<void> select(String sid, String profile) async => _unavailable();
  @override
  Future<void> stop(String sid) async => _unavailable();
  @override
  Future<void> approve(String sid, String id, bool approved) async =>
      _unavailable();
  @override
  Future<void> answer(String sid, String id, String answer) async =>
      _unavailable();
  @override
  Future<Json> settings() async => _unavailable();
  @override
  Future<Json> saveSettings(Json value) async => _unavailable();
  @override
  Future<void> deleteSession(String sid) async => _unavailable();
  @override
  void close() {}
}
