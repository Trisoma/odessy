import 'dart:async';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

// Opt-in: a real local Python service, never a live model/provider.
void main() {
  final endpoint = Platform.environment['ODESSY_TEST_ENDPOINT'];
  test('real authenticated Python session, SSE, next-turn selection and Stop',
      () async {
    final api = LocalApi(
        Uri.parse(endpoint!), Platform.environment['ODESSY_TEST_TOKEN']!);
    final session = await api.createSession();
    try {
      expect(await api.profiles(), contains('auto'));
      final result = Completer<void>();
      final subscription = api.events(session.id, 0).listen((e) {
        if (e.type == 'result' && !result.isCompleted) result.complete();
      });
      await api.send(session.id, 'hello');
      await result.future.timeout(const Duration(seconds: 20));
      final snapshot = await api.snapshot(session.id);
      expect(snapshot.messages.where((m) => m.role == 'user').length, 1);
      expect(snapshot.messages.where((m) => m.role == 'assistant').length, 1);
      await api.select(session.id, 'auto');
      expect((await api.snapshot(session.id)).selectedProfile, 'auto');
      await api.stop(session.id);
      await subscription.cancel();
    } finally {
      await api.deleteSession(session.id);
      api.close();
    }
  },
      skip: endpoint == null
          ? 'Set ODESSY_TEST_ENDPOINT and ODESSY_TEST_TOKEN for local integration.'
          : false);
}
