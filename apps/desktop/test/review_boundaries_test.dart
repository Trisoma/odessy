import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';

class GuardApi extends DemoApi {
  final gate = Completer<void>();
  bool closed = false;
  int approvals = 0;
  @override
  Future<void> deleteSession(String sid) => gate.future;
  @override
  Future<void> approve(String sid, String id, bool approved) async {
    approvals++;
  }

  @override
  void close() {
    closed = true;
    super.close();
  }
}

void main() {
  test('shutdown during replacement closes the abandoned next transport',
      () async {
    final old = GuardApi(), next = GuardApi(), concurrent = GuardApi();
    final c = DesktopSession(old);
    await c.start();
    final replace = c.replace(next);
    await Future<void>.delayed(Duration.zero);
    await c.replace(concurrent);
    expect(concurrent.closed, true);
    final shutdown = c.shutdown();
    old.gate.complete();
    await Future.wait([replace, shutdown]);
    expect(next.closed, true);
    expect(old.closed, true);
    c.dispose();
  });
  test('copied or stale pending maps cannot approve a reused request ID',
      () async {
    final api = GuardApi();
    final c = DesktopSession(api);
    await c.start();
    final data = <String, dynamic>{
      'id': 'same',
      'kind': 'approval',
      'allowed_channels': ['authenticated_ui'],
      'expires_at': DateTime.now().millisecondsSinceEpoch / 1000 + 30
    };
    final previous = Session('old', pending: [data]);
    c.session = Session('new', pending: [data]);
    await c.resolve(previous.pending.single, approved: true);
    expect(api.approvals, 0);
    expect(c.error, contains('no longer pending'));
    api.gate.complete();
    c.dispose();
  });
  test('bounded partial events preserve complete authoritative history', () {
    final session = Session.fromJson({
      'id': 's',
      'selected_profile': 'auto',
      'running': false,
      'history': [
        {'user': 'first', 'assistant': 'answer one'},
        {'user': 'second', 'assistant': 'answer two'}
      ],
      'events': [
        {
          'id': 20,
          'type': 'transcript',
          'data': {'role': 'assistant', 'text': 'answer two'}
        }
      ]
    });
    expect(session.messages.map((m) => m.text),
        ['first', 'answer one', 'second', 'answer two']);
  });
}
