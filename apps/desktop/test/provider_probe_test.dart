import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/settings/controllers/provider_probe_controller.dart';

class ProbeBackend implements SetupBackend {
  final requests = <SetupRequest>[];
  Completer<SetupReply>? gate;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    return gate == null
        ? SetupReply.fromJson({
            'id': request.id,
            'status': 'ok',
            'result': {'status': 'tested', 'message': 'Completed'}
          }, request.id)
        : await gate!.future;
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

void main() {
  test(
      'diagnostics use the selected section and no coordinator or implicit test',
      () async {
    final backend = ProbeBackend();
    final c = ProviderProbeController(backend);
    await c.run({}, 'tts');
    expect(backend.requests.single.operation, 'component-probe');
    expect(backend.requests.single.parameters['run'], false);
    expect(backend.requests.single.parameters['section'], 'tts');
    c.dispose();
  });
  test('editing invalidates in-flight test evidence', () async {
    final backend = ProbeBackend()..gate = Completer<SetupReply>();
    final c = ProviderProbeController(backend);
    final pending = c.run({}, 'llm', test: true);
    c.invalidate();
    final request = backend.requests.single;
    backend.gate!.complete(SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'status': 'tested'}
    }, request.id));
    await pending;
    expect(c.result, isEmpty);
    c.dispose();
  });
}
