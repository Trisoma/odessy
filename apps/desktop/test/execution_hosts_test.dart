import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/execution_hosts_controller.dart';
import 'package:odessy_desktop/features/devices/presentation/execution_hosts_page.dart';

class HostApi implements ExecutionHostsApi {
  bool declined = false;
  @override Future<Json> scanExecutionHosts() async => {'hosts': [
    {'id': 'stable-id', 'name': 'Execution host', 'host': '192.168.1.10', 'port': 8000}], 'connection': null};
  @override Future<Json> executionHostStatus() async => {'state': 'waiting_for_approval'};
  @override Future<Json> connectExecutionHost(String id) async {
    if (declined) throw const ExecutionConnectionFailure('Declined. Chat remains available.');
    return {'state': 'connected', 'host': {'id': id, 'name': 'Execution host'}, 'tools': 11};
  }
  @override Future<void> disconnectExecutionHost() async {}
}

void main() {
  testWidgets('auto detects and connects without secrets or a model session', (tester) async {
    final c = ExecutionHostsController(HostApi());
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ExecutionHostsPage(controller: c))));
    await tester.pumpAndSettle();
    expect(find.text('Execution host'), findsOneWidget);
    expect(find.textContaining('stable-id'), findsOneWidget);
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();
    expect(find.textContaining('11 tools'), findsOneWidget);
    expect(find.byType(TextField), findsNothing);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });
  test('decline restores retry controls without touching chat', () async {
    final c = ExecutionHostsController(HostApi()..declined = true);
    await c.scan();
    await c.connect('stable-id');
    expect(c.busy, false);
    expect(c.message, contains('Declined'));
    c.dispose();
  });
}
