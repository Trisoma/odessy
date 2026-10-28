import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test('connection credentials remain scoped, removable and session-only', () async {
    final store = MemoryCredentialStore();
    expect(store.persistsAcrossRestarts, isFalse);
    await store.write('http://127.0.0.1:8765', 'fixture-a');
    await store.write('http://127.0.0.1:8766', 'fixture-b');
    expect(await store.read('http://127.0.0.1:8765'), 'fixture-a');
    expect(await store.read('http://127.0.0.1:8766'), 'fixture-b');
    expect(await MemoryCredentialStore().read('http://127.0.0.1:8765'), isNull);
    await store.remove('http://127.0.0.1:8765');
    expect(await store.read('http://127.0.0.1:8765'), isNull);
    expect(await store.read('http://127.0.0.1:8766'), 'fixture-b');
  });
}
