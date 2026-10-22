import 'package:odessy_client/odessy_client.dart';

enum DemoScenario { empty, loading, offline, error, busy, approval, question }

/// Deterministic presentation stories. Never a fallback for a failed live client.
class ScenarioApi extends DemoApi {
  ScenarioApi(this.scenario)
      : super(
            delay: scenario == DemoScenario.busy
                ? const Duration(seconds: 5)
                : const Duration(milliseconds: 20));
  final DemoScenario scenario;
  @override
  Future<List<String>> profiles() async {
    if (scenario == DemoScenario.loading) {
      await Future<void>.delayed(const Duration(seconds: 3));
    }
    if (scenario == DemoScenario.offline) throw const ApiFailure(503);
    if (scenario == DemoScenario.error) throw const ApiFailure(500);
    return super.profiles();
  }

  @override
  Future<Session> createSession() async {
    final session = await super.createSession();
    switch (scenario) {
      case DemoScenario.busy:
        await send(session.id, 'Demo task in progress');
      case DemoScenario.approval:
        await send(session.id, '/approval');
        await Future<void>.delayed(const Duration(milliseconds: 30));
      case DemoScenario.question:
        await send(session.id, '/question');
        await Future<void>.delayed(const Duration(milliseconds: 30));
      default:
        break;
    }
    return snapshot(session.id);
  }
}
