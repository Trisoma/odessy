import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test('optional foundations advertise no unimplemented capability', () {
    const capabilities = AgentBasicsCapabilities();
    expect([
      capabilities.conversations,
      capabilities.customContinuation,
      capabilities.harnessContinuation,
      capabilities.accessModes,
      capabilities.context,
      capabilities.memory,
      capabilities.retrieval,
      capabilities.terminal,
      capabilities.checkpoints,
    ], everyElement(isFalse));
    expect(AccessMode.values.map((mode) => mode.name), ['ask', 'auto', 'full']);
  });
}
