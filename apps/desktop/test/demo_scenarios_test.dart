import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/demo/scenario_api.dart';

void main() {
  test('gallery failures are explicit and never change into live fallback',
      () async {
    for (final scenario in [DemoScenario.error, DemoScenario.offline]) {
      final api = ScenarioApi(scenario);
      expect(api.isDemo, true);
      await expectLater(api.profiles(), throwsA(isA<ApiFailure>()));
      api.close();
    }
  });
  test('gallery interaction stories use isolated pending requests', () async {
    for (final scenario in [DemoScenario.approval, DemoScenario.question]) {
      final api = ScenarioApi(scenario);
      final session = await api.createSession();
      expect(session.pending.single['kind'], scenario.name);
      await api.stop(session.id);
      expect((await api.snapshot(session.id)).pending, isEmpty);
      api.close();
    }
  });
}
