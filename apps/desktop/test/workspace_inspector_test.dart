import 'dart:async';
import 'dart:ui' as ui;
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/workspaces/controllers/workspace_inspector_controller.dart';
import 'package:odessy_desktop/features/workspaces/presentation/workspace_inspector.dart';
import 'package:odessy_desktop/app/theme.dart';

Json contextIdentity(String sid) => {
      'service_instance_id': 'instance',
      'session_id': sid,
      'workspace_id': 'workspace'
    };
Json descriptor(String sid, {bool changes = false}) => {
      ...contextIdentity(sid),
      'label': 'Offline test fixture · local workspace',
      'capabilities': {
        'read_files': true,
        'proposed_changes': changes,
        'recorded_output': true
      }
    };
Json textFile(String sid) => {
      ...contextIdentity(sid),
      'path': 'readme.txt',
      'text': 'Hello Odessy\n',
      'sha256': 'a' * 64,
      'truncated': false
    };

class InspectorApi extends DemoApi implements WorkspaceInspectorApi {
  bool grant = true, changes = false;
  int reads = 0, mutations = 0;
  Completer<WorkspaceTextFile>? delayed;
  @override
  String get identity => 'http://127.0.0.1:9000';
  @override
  Future<WorkspaceDescriptor> workspace(String sid) async {
    if (!grant) throw StateError('revoked');
    return WorkspaceDescriptor.fromJson(descriptor(sid, changes: changes));
  }

  @override
  Future<WorkspaceTree> listWorkspace(String sid, {String path = '.'}) async =>
      WorkspaceTree.fromJson({
        ...contextIdentity(sid),
        'path': path,
        'entries': [
          {'name': 'readme.txt', 'path': 'readme.txt', 'kind': 'file'}
        ],
        'truncated': false
      });
  @override
  Future<WorkspaceTextFile> readWorkspaceFile(String sid, String path) async {
    reads++;
    return delayed?.future ?? WorkspaceTextFile.fromJson(textFile(sid));
  }

  @override
  Future<Session> snapshot(String sid) async => Session(sid, pending: [
        {'id': 'approval', 'kind': 'approval'}
      ], events: [
        SessionEvent(1, 'result', {
          'tool_observations': [
            {
              'call_id': 'call-1',
              'request': {'name': 'terminal.run'},
              'result': {
                'stdout': 'Recorded fixture output\n',
                'stderr': '',
                'status': 'ok',
                'exit_code': 0
              }
            }
          ]
        })
      ]);
  @override
  Future<void> send(String sid, String text) async {
    mutations++;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    if (!const bool.fromEnvironment('CAPTURE_WORKSPACE')) return;
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('OdessyMono')
          ..addFont(rootBundle.load('assets/fonts/roboto-mono.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
  });
  late InspectorApi api;
  late WorkspaceInspectorController controller;
  setUp(() {
    api = InspectorApi();
    controller = WorkspaceInspectorController(
        api: api, sessionApi: api, serviceIdentity: api.identity);
  });
  tearDown(() {
    controller.dispose();
    api.close();
  });

  test(
      'explicit text selection/attachment; output comes from owned settled observations',
      () async {
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    expect(api.reads, 0);
    expect(api.mutations, 0);
    expect(controller.outputs.single.sessionId, 'sid');
    await controller.open('readme.txt', directory: false);
    controller.attachCurrent();
    expect(await controller.reviewedAttachmentText(), contains('Hello Odessy'));
    controller.removeAttachment('readme.txt');
    expect(controller.attachments, isEmpty);
    expect(api.mutations, 0);
  });

  test('late file from old target and disconnect are invalidated', () async {
    await controller.bind(sessionId: 'first', serviceInstanceId: 'instance');
    api.delayed = Completer<WorkspaceTextFile>();
    final task = controller.open('readme.txt', directory: false);
    await Future<void>.delayed(Duration.zero);
    await controller.bind(sessionId: 'second', serviceInstanceId: 'instance');
    api.delayed!.complete(WorkspaceTextFile.fromJson(textFile('first')));
    await task;
    expect(controller.file, isNull);
    expect(controller.descriptor!.sessionId, 'second');
    controller.invalidate();
    expect(controller.outputs, isEmpty);
    expect(controller.attachments, isEmpty);
  });

  test('permission revocation clears old contents, context and output',
      () async {
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    await controller.open('readme.txt', directory: false);
    controller.attachCurrent();
    api.grant = false;
    await controller.open('readme.txt', directory: false);
    expect(controller.file, isNull);
    expect(controller.tree, isNull);
    expect(controller.outputs, isEmpty);
    expect(controller.attachments, isEmpty);
    expect(controller.error, isNotNull);
  });

  test('old service and older snapshot versions cannot replace current output',
      () async {
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    controller.acceptSnapshot(
        Session('sid', events: [SessionEvent(20, 'result', {})]),
        serviceInstanceId: 'old-instance');
    expect(controller.outputs.single.callId, 'call-1');
    controller.acceptSnapshot(
        Session('sid', events: [SessionEvent(2, 'approval_answered', {})]),
        serviceInstanceId: 'instance');
    controller.acceptSnapshot(await api.snapshot('sid'),
        serviceInstanceId: 'instance');
    expect(controller.outputs, isEmpty);
  });

  test('pending plan uses broker ID rather than tool call ID', () async {
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    controller.acceptSnapshot(
        Session('sid', pending: [
          {
            'kind': 'approval',
            'id': 'broker-interaction',
            'proposed_change': {
              'session_id': 'sid',
              'request_id': 'tool-call',
              'path': 'readme.txt',
              'before_sha256': 'a' * 64,
              'after_sha256': 'b' * 64,
              'before_text': 'before',
              'after_text': 'after'
            }
          }
        ], events: [
          SessionEvent(2, 'approval_required', {})
        ]),
        serviceInstanceId: 'instance');
    expect(controller.changes.single.requestId, 'broker-interaction');
    expect(controller.canDecide(controller.changes.single), isFalse);
  });

  test(
      'proposals require exact pending ownership; no bypass when dispatch adapter is absent',
      () async {
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    ProposedChange proposal(String sid) => ProposedChange.fromJson({
          'session_id': sid,
          'request_id': 'approval',
          'path': 'readme.txt',
          'before_sha256': 'a' * 64,
          'after_sha256': 'b' * 64,
          'before_text': 'before',
          'after_text': 'after'
        });
    expect(() => controller.acceptChanges([proposal('foreign')]),
        throwsStateError);
    final change = proposal('sid');
    controller.acceptChanges([change]);
    expect(controller.canDecide(change), isFalse);
    await expectLater(controller.decide(change, true), throwsStateError);
    controller.acceptSnapshot(
        Session('sid', events: [SessionEvent(2, 'approval_answered', {})]),
        serviceInstanceId: 'instance');
    expect(controller.changes, isEmpty);
  });

  test(
      'configured approval callback receives exact sid/id once; stale validation never approves',
      () async {
    controller.dispose();
    final calls = <String>[];
    var valid = false;
    api.changes = true;
    controller = WorkspaceInspectorController(
        api: api,
        sessionApi: api,
        serviceIdentity: api.identity,
        validateProposal: (_) async => valid,
        resolveApproval: (sid, rid, approved) async =>
            calls.add('$sid/$rid/$approved'));
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    final p = ProposedChange.fromJson({
      'session_id': 'sid',
      'request_id': 'approval',
      'path': 'readme.txt',
      'before_sha256': 'a' * 64,
      'after_sha256': 'b' * 64,
      'before_text': 'before',
      'after_text': 'after'
    });
    controller.acceptChanges([p]);
    await controller.decide(p, true);
    expect(calls, isEmpty);
    valid = true;
    await controller.decide(p, true);
    expect(calls, ['sid/approval/true']);
    await expectLater(controller.decide(p, true), throwsStateError);
  });

  testWidgets(
      'narrow inspector tabs/close, readable output and no implicit execution',
      (tester) async {
    tester.view.physicalSize = const Size(420, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    var closed = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: WorkspaceInspector(
                controller: controller, onClose: () => closed++))));
    await tester.tap(find.text('readme.txt').first);
    await tester.pumpAndSettle();
    expect(find.text('Hello Odessy\n'), findsOneWidget);
    await tester.tap(find.text('Output'));
    await tester.pumpAndSettle();
    expect(find.text('Recorded fixture output\n'), findsOneWidget);
    await tester.tap(find.text('Changes'));
    await tester.pumpAndSettle();
    expect(find.textContaining('No bound pending'), findsOneWidget);
    await tester.tap(find.byTooltip('Close workspace panel'));
    expect(closed, 1);
    expect(tester.takeException(), isNull);
    expect(api.mutations, 0);
  });

  testWidgets('optional actual inspector captures', (tester) async {
    if (!const bool.fromEnvironment('CAPTURE_WORKSPACE')) return;
    tester.view.physicalSize = const Size(900, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await controller.bind(sessionId: 'sid', serviceInstanceId: 'instance');
    await controller.open('readme.txt', directory: false);
    const key = ValueKey('capture');
    final directory =
        Directory(const String.fromEnvironment('WORKSPACE_CAPTURE_OUTPUT'));
    await tester.runAsync(() => directory.create(recursive: true));
    controller.acceptChanges([
      ProposedChange.fromJson({
        'session_id': 'sid',
        'request_id': 'approval',
        'path': 'readme.txt',
        'before_sha256': 'a' * 64,
        'after_sha256': 'b' * 64,
        'before_text': 'Hello Odessy\n',
        'after_text': 'Hello Odessy\nReview the workspace before sending.\n'
      })
    ]);
    for (final panel in InspectorPanel.values) {
      controller.show(panel);
      await tester.pumpWidget(MaterialApp(
          theme: odessyTheme(Brightness.dark),
          home: Scaffold(
              body: RepaintBoundary(
                  key: key,
                  child: WorkspaceInspector(
                      controller: controller, onClose: () {})))));
      await tester.pumpAndSettle();
      final boundary =
          tester.renderObject<RenderRepaintBoundary>(find.byKey(key));
      await tester.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await File('${directory.path}/${panel.name}.png')
            .writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
    }
  });
}
