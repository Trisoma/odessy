import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/readiness_controller.dart';

class ObservedApi extends DemoApi implements RuntimeConfigurationApi {
  @override
  Future<RuntimeCapabilities> runtimeCapabilities() async =>
      RuntimeCapabilities.fromJson({
        'schema_version': 1,
        'service_instance_id': 'a' * 32,
        'restart_safe': true,
        'configuration': {
          'owner': 'b' * 64,
          'fingerprint': 'c' * 64,
          'fingerprints': {
            for (final s in ['llm', 'stt', 'tts', 'voice', 'host']) s: 'd' * 64
          },
          'model': {'provider': 'ollama', 'name': 'A'},
          'workspace': '/workspace',
          'role': 'combined'
        }
      });
  @override
  Future<String> prepareRestart(String instance) async => 'ticket';
  @override
  Future<void> resumeAdmission(String ticket) async {}
}

class DelayedObservation extends ObservedApi {
  final pending = Completer<RuntimeCapabilities>();
  @override
  Future<RuntimeCapabilities> runtimeCapabilities() => pending.future;
}

void main() {
  test(
      'replacing a pending observation clears loading and ignores late results',
      () async {
    final a = DelayedObservation(), r = ReadinessController();
    final first = r.bind(a);
    await r.bind(DemoApi());
    expect(r.busy, isFalse);
    a.pending.complete(await ObservedApi().runtimeCapabilities());
    await first;
    expect(r.current, isNull);
    r.dispose();
  });
  test(
      'saving B does not change observed startup A; owner/offline states stay distinct',
      () async {
    final r = ReadinessController();
    await r.bind(ObservedApi());
    final saved = {
      'configuration': {
        'owner': 'b' * 64,
        'fingerprint': 'e' * 64,
        'fingerprints': {
          for (final s in ['llm', 'stt', 'tts', 'voice', 'host'])
            s: s == 'llm' ? 'e' * 64 : 'd' * 64
        },
        'model': {'provider': 'ollama', 'name': 'B'},
        'workspace': '/workspace',
        'role': 'combined'
      }
    };
    expect(r.label('llm', saved, connected: true), 'Saved · pending restart');
    expect(r.label('tts', saved, connected: true),
        'Matches running configuration');
    expect(r.current!.configuration!.modelName, 'A');
    expect(r.label('llm', saved, connected: false),
        'Running configuration unknown · disconnected');
    (saved['configuration'] as Map)['owner'] = 'f' * 64;
    expect(r.label('llm', saved, connected: true),
        'Different configuration owner');
    r.dispose();
  });
  test('old APIs remain usable with unknown configuration', () async {
    final r = ReadinessController();
    await r.bind(DemoApi());
    expect(r.current, isNull);
    expect(r.error, isEmpty);
    r.dispose();
  });
}
