import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/execution_hosts_controller.dart';
import 'package:odessy_desktop/app/ssh_connection_controller.dart';
import 'package:odessy_desktop/features/devices/presentation/execution_hosts_page.dart';

final sshHostId = 'host_${List.filled(64, 'a').join()}';

class FakeExecutionHosts implements ExecutionHostsApi {
  @override
  Future<Json> scanExecutionHosts() async => {'hosts': <Json>[]};
  @override
  Future<Json> executionHostStatus() async => {'state': 'disconnected'};
  @override
  Future<Json> connectExecutionHost(String id) async =>
      {'host': {}, 'tools': 0};
  @override
  Future<void> disconnectExecutionHost() async {}
}

class FakeSsh implements SshConnectionApi {
  FakeSsh({this.known = true, this.changed = false, this.failStatus = 0});
  bool known, changed;
  final int failStatus;
  Json? currentTarget;
  Json? computer;
  Object? computerError;
  int computerCalls = 0;
  Json identity = {
    'alias': 'jetson',
    'hostname': '192.0.2.10',
    'user': 'tester',
    'port': 22,
    'endpoint': '192.0.2.10',
    'known': true,
    'known_fingerprints': ['SHA256:known'],
    'discovered_fingerprints': ['SHA256:known'],
    'changed': false,
    'discovery': 'ssh-keyscan (unauthenticated)',
  };

  @override
  Future<Json> sshAliases() async => {
        'schema_version': 1,
        'aliases': [
          {'alias': 'jetson', 'saved': currentTarget != null},
          {'alias': 'other', 'saved': false},
        ]
      };
  @override
  Future<Json> sshTarget() async => {
        'schema_version': 1,
        'target': currentTarget,
      };
  @override
  Future<Json> inspectSshAlias(String alias) async => {
        'schema_version': 1,
        'identity': {
          ...identity,
          'known': known,
          'changed': changed,
          'alias': alias
        },
      };
  @override
  Future<Json> testSshAlias(String alias) async {
    if (failStatus != 0) throw SshConnectionFailure(failStatus);
    return {
      'schema_version': 1,
      'test': {'ok': true, 'code': 'SSH_OK', 'elapsed_ms': 20.0},
      'identity': {...identity, 'known': known, 'changed': changed},
    };
  }

  @override
  Future<Json> saveSshTarget(String alias, {String displayName = ''}) async {
    currentTarget = {
      'alias': alias,
      'host_id': sshHostId,
      'display_name': alias,
      'monitoring': true,
      'config': '',
    };
    return {
      'schema_version': 1,
      'target': currentTarget,
      'test': {'ok': true}
    };
  }

  @override
  Future<Json> removeSshTarget() async {
    currentTarget = null;
    return {
      'schema_version': 1,
      'removed': true,
      'message': 'Removed from Odessy. This did not revoke your SSH key.'
    };
  }

  @override
  Future<Json> sshComputer() async {
    computerCalls++;
    if (computerError != null) throw computerError!;
    return computer ??
        {
          'schema_version': 1,
          'computer': {
            'method': 'ssh',
            'readiness': 'ready',
            'status': {
              'connectivity': 'online',
              'observed_at': '2026-11-11T12:00:00Z'
            },
            'capabilities': {
              'transport': true,
              'monitoring': true,
              'execution': false,
              'workspace': false
            },
          }
        };
  }
}

void main() {
  testWidgets('SSH happy path offers alias, review, test and save',
      (tester) async {
    final api = FakeSsh();
    final controller = SshConnectionController(api);
    final page = ExecutionHostsPage(
        controller: ExecutionHostsController(FakeExecutionHosts()),
        ssh: controller);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: page)));
    await tester.pumpAndSettle();

    expect(find.text('Connect a computer'), findsOneWidget);
    expect(find.text('Using SSH'), findsOneWidget);
    expect(find.text('Host key trusted in known_hosts'), findsOneWidget);
    await tester.ensureVisible(find.text('Test connection'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Test connection'));
    await tester.pumpAndSettle();
    expect(find.textContaining('SSH login verified'), findsOneWidget);
    await tester.ensureVisible(find.text('Save SSH computer'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save SSH computer'));
    await tester.pumpAndSettle();
    expect(find.text('Saved SSH computer'), findsOneWidget);
    expect(find.text('not available in this build'), findsOneWidget);
    expect(find.text('not required for status'), findsOneWidget);
    controller.dispose();
  });

  testWidgets('unknown host requires review and does not offer save/test',
      (tester) async {
    final api = FakeSsh(known: false);
    final controller = SshConnectionController(api);
    final page = ExecutionHostsPage(
        controller: ExecutionHostsController(FakeExecutionHosts()),
        ssh: controller);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: page)));
    await tester.pumpAndSettle();

    expect(find.text('Host key not trusted'), findsOneWidget);
    expect(find.textContaining('Verify this fingerprint independently'),
        findsOneWidget);
    expect(find.textContaining('ssh-keyscan'), findsWidgets);
    final testButton = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Test connection'));
    expect(testButton.onPressed, isNull);
  });

  testWidgets('changed host is visible and refuses save/test', (tester) async {
    final api = FakeSsh(changed: true);
    final controller = SshConnectionController(api);
    final page = ExecutionHostsPage(
        controller: ExecutionHostsController(FakeExecutionHosts()),
        ssh: controller);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: page)));
    await tester.pumpAndSettle();
    expect(find.text('Host key changed'), findsOneWidget);
    final saveButton = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Save SSH computer'));
    expect(saveButton.onPressed, isNull);
  });

  testWidgets('stale status shows last update and never claims online',
      (tester) async {
    final api = FakeSsh();
    api.currentTarget = {'alias': 'jetson', 'host_id': sshHostId};
    api.computer = {
      'schema_version': 1,
      'computer': {
        'method': 'ssh',
        'readiness': 'degraded',
        'status': {
          'connectivity': 'stale',
          'observed_at': '2026-11-11T12:00:00Z',
          'stale_groups': ['cpu']
        },
        'capabilities': {
          'transport': true,
          'monitoring': true,
          'execution': false,
          'workspace': false
        },
      }
    };
    final controller = SshConnectionController(api);
    final page = ExecutionHostsPage(
        controller: ExecutionHostsController(FakeExecutionHosts()),
        ssh: controller);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: page)));
    await tester.pumpAndSettle();
    expect(find.textContaining('stale · degraded'), findsOneWidget);
    expect(find.textContaining('Last status: 2026-11-11T12:00:00Z'),
        findsOneWidget);
    expect(find.textContaining('Stale groups: cpu'), findsOneWidget);
    api.computer = {
      'schema_version': 1,
      'computer': {
        'method': 'ssh',
        'readiness': 'ready',
        'status': {
          'connectivity': 'online',
          'observed_at': '2026-11-11T12:00:10Z',
          'stale_groups': []
        },
        'capabilities': {
          'transport': true,
          'monitoring': true,
          'execution': false,
          'workspace': false
        },
      }
    };
    await tester.ensureVisible(find.text('Refresh status'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Refresh status'));
    await tester.pumpAndSettle();
    expect(find.textContaining('online · ready'), findsOneWidget);
    controller.dispose();
  });
  testWidgets('saved target removal explains OS access remains',
      (tester) async {
    final api = FakeSsh();
    api.currentTarget = {'alias': 'jetson', 'host_id': sshHostId};
    final controller = SshConnectionController(api);
    final page = ExecutionHostsPage(
        controller: ExecutionHostsController(FakeExecutionHosts()),
        ssh: controller);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: page)));
    await tester.pumpAndSettle();
    expect(find.text('Remove SSH computer'), findsOneWidget);
    await tester.ensureVisible(find.text('Remove SSH computer'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Remove SSH computer'));
    await tester.pumpAndSettle();
    expect(find.text('Remove SSH computer?'), findsOneWidget);
    await tester.tap(find.text('Remove'));
    await tester.pumpAndSettle();
    expect(find.textContaining('did not revoke your SSH key'), findsOneWidget);
    controller.dispose();
  });
  test('service failures become one actionable recovery message', () async {
    final controller = SshConnectionController(FakeSsh(failStatus: 503));
    await controller.load();
    expect(controller.selected, isNotNull);
    await controller.testConnection();
    expect(controller.error, contains('SSH service is unavailable'));
    controller.dispose();
  });

  test('cancel stops waiting without claiming the remote check ended',
      () async {
    final gate = Completer<Json>();
    final api = _SlowSsh(gate);
    final controller = SshConnectionController(api);
    await controller.load();
    final pending = controller.testConnection();
    expect(controller.busy, isTrue);
    controller.cancel();
    expect(controller.busy, isFalse);
    expect(controller.message, contains('bounded check'));
    gate.complete({
      'schema_version': 1,
      'test': {'ok': true}
    });
    await pending;
    controller.dispose();
  });
  test('status failure marks old observation unavailable and keeps last update',
      () async {
    final api = FakeSsh();
    api.currentTarget = {'alias': 'jetson', 'host_id': sshHostId};
    final controller = SshConnectionController(api);
    await controller.load();
    expect(controller.status?['readiness'], 'ready');
    api.computerError = const SshConnectionFailure(503);
    await controller.refreshStatus();
    expect(
        controller.statusError, contains('monitoring service is not running'));
    expect(controller.status?['readiness'], 'unavailable');
    expect(controller.status?['status']?['connectivity'], 'unknown');
    expect(
        controller.status?['status']?['observed_at'], '2026-11-11T12:00:00Z');
    controller.dispose();
  });

  test('failed refresh does not repopulate after removal', () async {
    final gate = Completer<Json>();
    final api = _DelayedComputerSsh();
    api.currentTarget = {'alias': 'jetson', 'host_id': sshHostId};
    final controller = SshConnectionController(api);
    await controller.load();
    api.nextComputer = gate.future;
    final pending = controller.refreshStatus();
    await Future<void>.delayed(Duration.zero);
    await controller.remove();
    gate.complete({
      'schema_version': 1,
      'computer': {
        'readiness': 'ready',
        'status': {
          'connectivity': 'online',
          'observed_at': '2026-11-11T12:00:00Z'
        }
      }
    });
    await pending;
    expect(controller.target, isNull);
    expect(controller.status, isNull);
    controller.dispose();
  });

  test('overlapping status refreshes share one request', () async {
    final gate = Completer<Json>();
    final api = _DelayedComputerSsh();
    api.currentTarget = {'alias': 'jetson', 'host_id': sshHostId};
    final controller = SshConnectionController(api);
    await controller.load();
    final before = api.computerCalls;
    api.nextComputer = gate.future;
    final first = controller.refreshStatus();
    final second = controller.refreshStatus();
    await Future<void>.delayed(Duration.zero);
    expect(api.computerCalls - before, 1);
    gate.complete({
      'schema_version': 1,
      'computer': {
        'readiness': 'ready',
        'status': {
          'connectivity': 'online',
          'observed_at': '2026-11-11T12:00:00Z'
        }
      }
    });
    await Future.wait([first, second]);
    controller.dispose();
  });

  test('rebind ignores a delayed response from the previous service', () async {
    final old = _DelayedComputerSsh();
    old.currentTarget = {'alias': 'jetson', 'host_id': sshHostId};
    final controller = SshConnectionController(old);
    await controller.load();
    final gate = Completer<Json>();
    old.nextComputer = gate.future;
    final pending = controller.refreshStatus();
    await Future<void>.delayed(Duration.zero);
    final replacement = FakeSsh();
    final otherId = 'host_${String.fromCharCodes(List.filled(64, 98))}';
    replacement.currentTarget = {'alias': 'other', 'host_id': otherId};
    replacement.computer = {
      'schema_version': 1,
      'computer': {
        'readiness': 'ready',
        'status': {
          'connectivity': 'online',
          'observed_at': '2026-11-11T12:00:09Z',
        },
      }
    };
    controller.bind(replacement);
    await Future<void>.delayed(const Duration(milliseconds: 10));
    gate.complete({
      'schema_version': 1,
      'computer': {
        'readiness': 'ready',
        'status': {
          'connectivity': 'online',
          'observed_at': '2026-11-11T12:00:00Z'
        }
      }
    });
    await pending;
    expect(controller.target?['target']?['alias'], 'other');
    expect(
        controller.status?['status']?['observed_at'], '2026-11-11T12:00:09Z');
    controller.dispose();
  });

  test('cancel during save says the service may still finish saving', () async {
    final gate = Completer<Json>();
    final api = _SlowSaveSsh(gate);
    final controller = SshConnectionController(api);
    await controller.load();
    final pending = controller.save();
    await Future<void>.delayed(Duration.zero);
    controller.cancel();
    expect(controller.message, contains('may still finish saving'));
    gate.complete({
      'schema_version': 1,
      'target': {'alias': 'jetson', 'host_id': sshHostId}
    });
    await pending;
    expect(controller.hasTarget, isFalse);
    controller.dispose();
  });
}

class _SlowSsh extends FakeSsh {
  _SlowSsh(this.gate);
  final Completer<Json> gate;
  @override
  Future<Json> testSshAlias(String alias) => gate.future;
}

class _DelayedComputerSsh extends FakeSsh {
  Future<Json>? nextComputer;

  @override
  Future<Json> sshComputer() {
    final pending = nextComputer;
    if (pending != null) {
      computerCalls++;
      return pending;
    }
    return super.sshComputer();
  }
}

class _SlowSaveSsh extends FakeSsh {
  _SlowSaveSsh(this.gate);
  final Completer<Json> gate;

  @override
  Future<Json> saveSshTarget(String alias, {String displayName = ''}) async {
    final result = await gate.future;
    currentTarget = result['target'];
    return result;
  }
}
