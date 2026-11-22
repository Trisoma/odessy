import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/ssh_connection_controller.dart';
import 'package:odessy_desktop/app/execution_hosts_controller.dart';
import 'package:odessy_desktop/features/devices/presentation/execution_hosts_page.dart';
import 'ssh_connection_test.dart' show FakeSsh, FakeExecutionHosts;

class ExecutionSsh extends FakeSsh implements SshExecutionApi {
  Json? execution;
  bool refuse = false;
  @override
  Future<Json> sshExecutionStatus() async => {'execution': execution};
  @override
  Future<Json> enableSshExecution() async {
    if (refuse) {
      throw const SshConnectionFailure(409, 'Reconnect before execution.');
    }
    execution = {'target': 'fixture', 'execution': true, 'sandbox': 'none'};
    return {'execution': execution};
  }

  @override
  Future<Json> disableSshExecution() async {
    execution = null;
    return {'execution': null};
  }
}

class FailedReconnect extends ExecutionSsh {
  FailedReconnect(this.throws);
  final bool throws;
  @override
  Future<Json> connectSsh(String target,
      {int? port, String password = ''}) async {
    execution =
        null; // The service revokes execution before any reconnect probe.
    if (throws) throw const SshConnectionFailure(409, 'Host key changed.');
    return {
      'connected': false,
      'test': {'message': 'Authentication failed.'}
    };
  }
}

void main() {
  for (final throws in [false, true]) {
    test('failed reconnect refreshes revoked execution (throws=$throws)',
        () async {
      final api = FailedReconnect(throws);
      var reloads = 0;
      final controller =
          SshConnectionController(api, onExecutionChanged: () async {
        reloads++;
      });
      await controller.setExecution(true);
      await controller.connect('tester@192.0.2.10');
      expect(controller.execution, isNull);
      expect(reloads, 2);
      expect(controller.error, isNotEmpty);
      controller.dispose();
    });
  }

  test('execution acknowledges service and reloads tools once per change',
      () async {
    final api = ExecutionSsh();
    var reloads = 0;
    final c = SshConnectionController(api, onExecutionChanged: () async {
      reloads++;
    });
    await c.setExecution(true);
    expect(c.execution?['target'], 'fixture');
    expect(reloads, 1);
    await c.setExecution(false);
    expect(c.execution, isNull);
    expect(reloads, 2);
    c.dispose();
  });
  test('refused selection remains disconnected with real error', () async {
    final api = ExecutionSsh()..refuse = true;
    var reloads = 0;
    final c = SshConnectionController(api, onExecutionChanged: () async {
      reloads++;
    });
    await c.setExecution(true);
    expect(c.execution, isNull);
    expect(c.error, 'Reconnect before execution.');
    expect(reloads, 0);
    c.dispose();
  });
  testWidgets('connected target has explicit command selection and limitations',
      (t) async {
    t.view.physicalSize = const Size(1100, 1800);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final api = ExecutionSsh();
    await api.connectSsh('tester@192.0.2.10');
    final c = SshConnectionController(api);
    final hosts = ExecutionHostsController(FakeExecutionHosts());
    await t.pumpWidget(MaterialApp(
        home: Scaffold(body: ExecutionHostsPage(controller: hosts, ssh: c))));
    await t.pumpAndSettle();
    final button = find.byKey(const Key('ssh-execution'));
    await t.ensureVisible(button);
    expect(find.text('Use for agent commands'), findsOneWidget);
    await t.tap(button);
    await t.pumpAndSettle();
    expect(find.text('Disconnect agent commands'), findsOneWidget);
    expect(find.textContaining('WITHOUT a remote sandbox'), findsOneWidget);
    expect(c.execution?['execution'], true);
    await t.pumpWidget(const SizedBox.shrink());
    c.dispose();
    hosts.dispose();
  });
}
