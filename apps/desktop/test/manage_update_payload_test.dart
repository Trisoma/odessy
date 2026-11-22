import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';
import 'installed_manage_controller_test.dart' show Backend;

class UpdateBackend extends Backend {
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.parameters['action'] == 'context') {
      return SetupReply.fromJson({
        'id': request.id,
        'status': 'ok',
        'result': {'root': 'C:/Odessy/App'}
      }, request.id);
    }
    return super.execute(request);
  }
}

void main() {
  testWidgets(
      'manage launch carries explicit update payload through review and apply',
      (tester) async {
    final backend = UpdateBackend()..installedPayload = 'C:/old-payload';
    backend.status['payload'] = 'C:/old-payload';
    await tester.pumpWidget(SetupApplication(
        mode: 'manage',
        installRoot: 'C:/Odessy/App',
        payloadRoot: 'C:/new-payload',
        backendFactory: () => backend));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Repair or update files'));
    await tester.tap(find.text('Repair or update files'));
    await tester.pumpAndSettle();
    expect(backend.requests.last.parameters['payload'], 'C:/new-payload');
    await tester.ensureVisible(find.text('Confirm once'));
    await tester.tap(find.text('Confirm once'));
    await tester.pumpAndSettle();
    expect(
        backend.requests
            .singleWhere((r) => r.parameters['action'] == 'package-repair')
            .parameters['payload'],
        'C:/new-payload');
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
