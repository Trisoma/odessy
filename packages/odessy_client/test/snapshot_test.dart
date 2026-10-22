import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test('snapshots freeze nested JSON and source collections', () {
    final data = <String, dynamic>{
      'nested': <dynamic>[1]
    };
    final events = <SessionEvent>[SessionEvent(2, 'x', data)];
    final snapshot = Session('a', pending: [data], events: events);
    (data['nested'] as List).add(2);
    events.clear();
    expect(snapshot.events, hasLength(1));
    expect(snapshot.pending.single['nested'], [1]);
    expect(() => (snapshot.pending.single['nested'] as List).add(3),
        throwsUnsupportedError);
    expect(() => snapshot.events.single.data['x'] = 1, throwsUnsupportedError);
  });
  test(
      'reducer rejects identity changes and stale versions and deduplicates IDs',
      () {
    final current = Session('a', events: [SessionEvent(3, 'x', {})]);
    expect(identical(reduceSessionSnapshot(current, Session('a')), current),
        isTrue);
    expect(
        () => reduceSessionSnapshot(current, Session('b')), throwsStateError);
    final next = reduceSessionSnapshot(
        current,
        Session('a', events: [
          SessionEvent(4, 'x', {}),
          SessionEvent(3, 'x', {}),
          SessionEvent(4, 'x', {})
        ]));
    expect(next.events.map((e) => e.id), [3, 4]);
  });
}
