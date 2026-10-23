import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

Future<Session> waitFor(
    LocalApi api, String sid, bool Function(Session) predicate) async {
  for (var i = 0; i < 100; i++) {
    final value = await api.snapshot(sid);
    if (predicate(value)) return value;
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  throw StateError('Fixture session did not reach expected state');
}

void main() {
  final enabled = Platform.environment['ODESSY_BROKER_FIXTURE'] == '1';
  test(
      'real broker approval/question, duplicate refusal, settings and cancellation',
      () async {
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:8876'), 'desktop-fixture-only');
    final session = await api.createSession();
    try {
      await api.send(session.id, '/approval');
      final approval =
          (await waitFor(api, session.id, (s) => s.pending.isNotEmpty))
              .pending
              .single;
      await api.approve(session.id, approval['id'], false);
      await waitFor(api, session.id, (s) => !s.running);
      await expectLater(api.approve(session.id, approval['id'], true),
          throwsA(isA<ApiFailure>()));
      await api.send(session.id, '/question');
      final question =
          (await waitFor(api, session.id, (s) => s.pending.isNotEmpty))
              .pending
              .single;
      expect(question['choices'], contains('Odessy'));
      await api.answer(session.id, question['id'], 'Odessy');
      await waitFor(api, session.id, (s) => !s.running);
      final config = await api.settings();
      (config['settings'] as Map)['max_consultations'] = 2;
      await api.saveSettings(config);
      expect(
          ((await api.settings())['settings'] as Map)['max_consultations'], 2);
      await api.send(session.id, '/slow');
      await waitFor(api, session.id, (s) => s.running);
      await api.stop(session.id);
      expect((await api.snapshot(session.id)).running, false);
    } finally {
      await api.deleteSession(session.id);
      api.close();
    }
  },
      skip: !enabled
          ? 'Run tool/desktop_fixture_server.py and set ODESSY_BROKER_FIXTURE=1.'
          : false);
}
