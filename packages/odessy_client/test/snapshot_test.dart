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
  test('session engine identity is parsed, read-only and survives reduction', () {
    final parsed = Session.fromJson({
      'id': 'a',
      'selected_profile': 'auto',
      'engine': {
        'name': 'harness',
        'source': 'explicit',
        'identity': {'version': '0.2.1-rc.2'},
        'capabilities': {
          'engine': 'harness',
          'live_text_deltas': true,
          'questions': false,
        },
      },
    });
    expect(parsed.engine?.name, 'harness');
    expect(parsed.engine?.source, 'explicit');
    expect(parsed.engine?.identity['version'], '0.2.1-rc.2');
    expect(parsed.engine?.supports('live_text_deltas'), isTrue);
    expect(parsed.engine?.supports('questions'), isFalse);
    expect(parsed.engine?.unsupported, ['questions']);
    expect(() => parsed.engine!.capabilities['questions'] = true,
        throwsUnsupportedError);
    expect(reduceSessionSnapshot(parsed, parsed).engine?.name, 'harness');
  });
  test('a snapshot from an older service simply has no engine block', () {
    final parsed = Session.fromJson({
      'id': 'a',
      'selected_profile': 'auto',
      'engine': 'legacy',
    });
    expect(parsed.engine, isNull);
    expect(reduceSessionSnapshot(null, parsed).engine, isNull);
  });
}
