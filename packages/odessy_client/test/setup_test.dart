import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('setup request freezes nested selection', () {
    final value = <String, dynamic>{
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': 'original'},
    };
    final request = SetupRequest(
      id: 'one',
      operation: 'review',
      selection: value,
    );
    (value['model'] as Map)['name'] = 'changed';
    expect((request.selection['model'] as Map)['name'], 'original');
    expect(
      () => (request.selection['model'] as Map)['name'] = 'changed',
      throwsUnsupportedError,
    );
  });
  test('wrong request identity or malformed response never succeeds', () {
    expect(
      () => SetupReply.fromJson({
        'id': 'old',
        'status': 'ok',
        'result': {},
      }, 'new'),
      throwsFormatException,
    );
    expect(
      () => SetupReply.fromJson({
        'id': 'one',
        'status': 'ok',
        'result': true,
      }, 'one'),
      throwsFormatException,
    );
    final failure = SetupReply.fromJson({
      'id': 'one',
      'status': 'error',
      'error': 'secret trace',
    }, 'one');
    expect(failure.error, isNot(contains('secret')));
  });
}
