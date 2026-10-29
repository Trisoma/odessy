import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';

void main() {
  test('provider failure is shown from sanitized backend error event',
      () async {
    final api = ReconnectApi();
    final c = DesktopSession(api);
    await c.start();
    api.first.add(SessionEvent(1, 'error', {
      'message': 'deepseek API call failed (400): invalid model identifier'
    }));
    await Future<void>.delayed(Duration.zero);
    expect(c.error, contains('invalid model identifier'));
    expect(c.status, 'Failed');
    c.dispose();
  });

  test('picker labels use actual provider and model metadata', () async {
    final c = DesktopSession(MetadataApi());
    await c.start();
    expect(c.profileLabel('current'), contains('deepseek-flash'));
    expect(c.profileLabel('current'), contains('deepseek'));
    expect(c.profiles, contains('current'));
    c.dispose();
  });
  test('LAN pairing request is held, sanitized, and answered exactly once',
      () async {
    final api = PairingTransport();
    final c = DesktopSession(api);
    await c.start();
    api.emit(SessionEvent(1, 'pairing_request',
        {'id': 'a1', 'name': 'Jetson\u0007', 'ip': '192.168.1.50'}));
    await Future<void>.delayed(Duration.zero);
    expect(c.pairing?.id, 'a1');
    expect(c.pairing?.name, 'Jetson', reason: 'control characters are stripped');
    expect(await c.answerPairing('a1', true), true);
    expect(api.answers, {'a1': true});
    expect(c.pairing, isNull);
    expect(await c.answerPairing('gone', false), false,
        reason: 'a request that is no longer held is not re-sent');
    expect(api.answers, {'a1': true});
    c.dispose();
  });

  test('a transport that cannot answer pairing refuses instead of pretending',
      () async {
    final api = ReconnectApi();
    final c = DesktopSession(api);
    await c.start();
    api.first.add(SessionEvent(
        1, 'pairing_request', {'id': 'a1', 'name': 'Jetson', 'ip': '10.0.0.2'}));
    await Future<void>.delayed(Duration.zero);
    expect(c.pairing?.id, 'a1');
    expect(await c.answerPairing('a1', true), false);
    expect(c.error, isNotEmpty);
    c.dispose();
  });

  test('shutdown is awaitable and idempotent', () async {
    final api = LifecycleApi();
    final c = DesktopSession(api);
    await c.start();
    await Future.wait([c.shutdown(), c.shutdown()]);
    expect(api.deletes, 1);
    expect(api.closes, 1);
    expect(c.connected, false);
    expect(await c.send('after shutdown'), false);
    c.dispose();
  });
  test('memory credentials are isolated and explicitly ephemeral', () async {
    final store = MemoryCredentialStore();
    await store.write('local', 'secret');
    expect(store.persistsAcrossRestarts, false);
    expect(await store.read('remote'), isNull);
    expect(await MemoryCredentialStore().read('local'), isNull);
    await store.remove('local');
    expect(await store.read('local'), isNull);
  });
  test('reconnect restores the same session without resubmitting', () async {
    final api = ReconnectApi();
    final c = DesktopSession(api);
    await c.start();
    await c.send('hello');
    api.first.addError(const ApiFailure(500));
    await Future<void>.delayed(const Duration(milliseconds: 2200));
    expect(c.connected, true);
    expect(api.sends, 1);
    expect(api.creates, 1);
    expect(c.session!.messages.where((m) => m.role == 'assistant').length, 1);
    c.dispose();
  });
  test('failed Stop does not claim cancellation', () async {
    final c = DesktopSession(FailedStopApi());
    await c.start();
    await c.send('hello');
    await c.stop();
    expect(c.session!.running, true);
    expect(c.status.startsWith('Stopped'), false);
    expect(c.error, isNotEmpty);
    c.dispose();
  });
  test('settings transaction cannot follow a connection replacement', () async {
    final first = DelayedSettingsApi();
    final second = DelayedSettingsApi();
    final c = DesktopSession(first);
    await c.start();
    final save = c.updateConsultationLimit(2);
    final outcome = expectLater(save, throwsStateError);
    await c.replace(second);
    first.gate.complete({
      'settings': {'max_consultations': 1},
      'participants': {}
    });
    await outcome;
    expect(first.writes, 0);
    expect(second.writes, 0);
    c.dispose();
  });
  test('one foreground turn, pinned model and stale response protection',
      () async {
    final c = DesktopSession(DemoApi(delay: const Duration(milliseconds: 30)));
    await c.start();
    expect(await c.send('hello'), true);
    expect(await c.send('must not run'), false);
    await c.select('local');
    expect(c.session!.activeProfile, 'auto');
    expect(c.session!.selectedProfile, 'local');
    await c.stop();
    await c.resolve({'id': 'obsolete'}, approved: true);
    expect(c.error, contains('no longer pending'));
    c.dispose();
  });
  test('switching connection during an active turn is refused', () async {
    final c = DesktopSession(DemoApi());
    await c.start();
    await c.send('hello');
    final original = c.api;
    await c.replace(DemoApi());
    expect(identical(c.api, original), true);
    expect(c.error, contains('Stop'));
    c.dispose();
  });
}

class FailedStopApi extends DemoApi {
  @override
  Future<void> stop(String sid) async => throw const ApiFailure(500);
}

class PairingTransport extends DemoApi implements PairingApi {
  PairingTransport() : super(delay: const Duration(milliseconds: 20));
  final _events = StreamController<SessionEvent>.broadcast();
  final answers = <String, bool>{};

  void emit(SessionEvent event) => _events.add(event);

  @override
  Stream<SessionEvent> events(String sid, int after) => _events.stream;

  @override
  Future<void> approvePairing(String id, bool accepted) async {
    answers[id] = accepted;
  }

  @override
  void close() {
    unawaited(_events.close());
    super.close();
  }
}

class ReconnectApi extends DemoApi {
  ReconnectApi() : super(delay: const Duration(milliseconds: 20));
  final first = StreamController<SessionEvent>();
  int sends = 0, creates = 0, streams = 0;
  @override
  Future<Session> createSession() {
    creates++;
    return super.createSession();
  }

  @override
  Future<void> send(String sid, String text) {
    sends++;
    return super.send(sid, text);
  }

  @override
  Stream<SessionEvent> events(String sid, int after) =>
      streams++ == 0 ? first.stream : super.events(sid, after);
  @override
  void close() {
    unawaited(first.close());
    super.close();
  }
}

class DelayedSettingsApi extends DemoApi {
  final gate = Completer<Json>();
  int writes = 0;
  @override
  Future<Json> settings() => gate.future;
  @override
  Future<Json> saveSettings(Json value) async {
    writes++;
    return value;
  }
}

class LifecycleApi extends DemoApi {
  int deletes = 0, closes = 0;
  @override
  Future<void> deleteSession(String sid) async {
    deletes++;
  }

  @override
  void close() {
    closes++;
    super.close();
  }
}

class MetadataApi extends DemoApi implements ProfileMetadataApi {
  @override
  Future<List<String>> profiles() async => ['current'];
  @override
  Future<List<Profile>> profileMetadata() async => [
        Profile('current', {'provider': 'deepseek', 'model': 'deepseek-flash'})
      ];
}
