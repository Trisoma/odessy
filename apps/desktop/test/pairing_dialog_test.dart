import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/execution_hosts_controller.dart';
import 'package:odessy_desktop/app/session_controller.dart';

// The executor terminal owns Y/N approval. Legacy session events must neither
// reopen the retired approval dialog nor authorize a connection.
class HostTransport extends DemoApi implements ExecutionHostsApi {
  HostTransport() : super(delay: const Duration(milliseconds: 20));
  final incoming = StreamController<SessionEvent>.broadcast();
  @override
  Stream<SessionEvent> events(String sid, int after) => incoming.stream;
  @override
  Future<Json> scanExecutionHosts() async => {'hosts': <Json>[]};
  @override
  Future<Json> executionHostStatus() async => {'state': 'disconnected'};
  @override
  Future<Json> connectExecutionHost(String id) async =>
      throw const ExecutionConnectionFailure('Executor unavailable');
  @override
  Future<void> disconnectExecutionHost() async {}
  @override
  void close() {
    unawaited(incoming.close());
    super.close();
  }
}

void main() {
  testWidgets('retired pairing events cannot open an assistant approval dialog',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = HostTransport();
    await tester.pumpWidget(OdessyApp(api: api));
    await tester.pumpAndSettle();
    api.incoming.add(SessionEvent(1, 'pairing_request',
        {'id': 'old', 'name': 'Unverified device', 'ip': '192.168.1.50'}));
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
    expect(find.text('Incoming connection request'), findsNothing);
    expect(find.text('Assistant'), findsWidgets);
    await tester.pumpWidget(const SizedBox());
  });

  test('failed host connection preserves an already usable conversation',
      () async {
    final api = HostTransport();
    final chat = DesktopSession(api);
    final hosts = ExecutionHostsController(api);
    await chat.start();
    final sessionId = chat.session!.id;
    await hosts.connect('offline-host');
    expect(hosts.busy, false);
    expect(hosts.message, contains('unavailable'));
    expect(chat.connected, true);
    expect(chat.session!.id, sessionId);
    expect(await chat.send('Hello without an executor'), true);
    hosts.dispose();
    await chat.shutdown();
    chat.dispose();
  });
}
