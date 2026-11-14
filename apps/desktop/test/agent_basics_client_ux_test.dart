import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/history/controllers/basics_controller.dart';
import 'package:odessy_desktop/features/history/presentation/basics_page.dart';

const cid = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    pid = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    tid = 'cccccccccccccccccccccccccccccccc';
const date = '2026-11-14T09:00:00Z';
Json row({int revision = 2, String engine = 'legacy'}) => {
      'version': 1,
      'id': cid,
      'project_id': pid,
      'engine': engine,
      'profile': 'local',
      'title': 'Saved boat',
      'pinned': false,
      'archived': false,
      'revision': revision,
      'created_at': date,
      'updated_at': date,
      'continuation': engine == 'harness' ? 'history_only' : 'continuable'
    };
Json event(int n, String kind, {String? turn, String text = ''}) => {
      'sequence': n,
      'event_id': '${turn ?? 'turn'}:$kind:$n',
      'kind': kind,
      'created_at': date,
      'payload': {'text': text}
    };

class WireFixture {
  String instance = 'service-1', engine = 'legacy';
  bool advanced = false, failMode = false, failDelete = false;
  bool wrongProjectMemory = false;
  Json fact = {
    'version': 1,
    'id': tid,
    'project_id': pid,
    'text': 'User-created fact',
    'enabled': true,
    'revision': 0,
    'created_at': date,
    'updated_at': date,
    'source': 'user'
  };
  int modeRevision = 0;
  String mode = 'ask';
  final requests = <({String path, String method, Json? body})>[];
  Completer<Json>? delayedMode;
  Json capabilities() => {
        'version': 1,
        'service_instance_id': instance,
        'conversations': true,
        'custom_continuation': true,
        'harness_continuation': false,
        'access_modes': true,
        'context': advanced,
        'memory': advanced,
        'retrieval': advanced,
        'terminal': advanced,
        'checkpoints': advanced
      };
  Json modeData(String sid) => {
        'version': 1,
        'session_id': sid,
        'service_instance_id': instance,
        'mode': mode,
        'revision': modeRevision,
        'supported_modes': engine == 'harness' ? [] : ['ask', 'auto', 'full'],
        'reviewer': 'policy-v1',
        'scope': {'workspace_id': 'root-a', 'target_id': 'local'},
        'effective': engine != 'harness'
      };
  Json handle({String status = 'running'}) => {
        'version': 1,
        'id': tid,
        'session_id': 's1',
        'service_instance_id': instance,
        'revision': 1,
        'status': status,
        'exit_code': status == 'running' ? null : 0
      };
  Future<Json> request(String path, String method, Json? body) async {
    requests.add((path: path, method: method, body: body));
    final uri = Uri.parse(path), p = Uri.parse(path).path;
    if (p.endsWith('capabilities')) return capabilities();
    if (p.endsWith('access-mode')) {
      if (method == 'PUT') {
        if (failMode) throw const ApiFailure(409);
        if (delayedMode != null) return delayedMode!.future;
        mode = body!['mode'] as String;
        modeRevision++;
      }
      return modeData(p.split('/')[3]);
    }
    if (p == '/v1/projects') {
      return {
        'version': 1,
        'projects': [
          {
            'version': 1,
            'id': pid,
            'label': 'Odessy project',
            'created_at': date
          }
        ]
      };
    }
    if (p == '/v1/conversations/search') {
      return {
        'version': 1,
        'matches': [
          {
            'conversation_id': cid,
            'sequence': 1,
            'snippet': 'needle in complete transcript'
          }
        ],
        'next_cursor': null
      };
    }
    if (p == '/v1/conversations') {
      return {
        'version': 1,
        'conversations': [row(engine: engine)],
        'next_cursor': uri.queryParameters['cursor'] == null ? 'page-two' : null
      };
    }
    if (p.endsWith('/messages')) {
      return {
        'version': 1,
        'conversation_id': cid,
        'messages': uri.queryParameters['before'] == null
            ? [
                event(5, 'user', text: 'hello'),
                event(6, 'assistant', text: 'settled answer')
              ]
            : [event(1, 'user', text: 'older')],
        'next_before': uri.queryParameters['before'] == null ? 5 : null
      };
    }
    if (p.endsWith('/resume')) {
      return {
        'version': 1,
        'conversation_id': cid,
        'session_id': 'resumed-session',
        'service_instance_id': instance,
        'continuation': 'continuable'
      };
    }
    if (p.endsWith('/export')) {
      return {'version': 1, 'conversation': row(), 'events': []};
    }
    if (p.endsWith('/bind-workspace')) return row(revision: 3);
    if (p.endsWith('/context')) {
      return {
        'version': 1,
        'conversation_id': cid,
        'revision': 2,
        'summary': null,
        'memory_ids': [],
        'citations': [],
        'chars_used': 0,
        'budget_chars': 24000,
        'method': 'none'
      };
    }
    if (p.endsWith('/memory')) {
      if (method == 'POST') {
        fact = {...fact, 'text': body!['text']};
        return fact;
      }
      return {
        'version': 1,
        'items': [
          if (fact.isNotEmpty)
            {...fact, if (wrongProjectMemory) 'project_id': 'd' * 32}
        ]
      };
    }
    if (p.endsWith('/memory/$tid')) {
      if (method == 'DELETE') {
        fact = {};
        return {};
      }
      fact = {
        ...fact,
        'revision': (fact['revision'] as int) + 1,
        if (body!.containsKey('text')) 'text': body['text'],
        if (body.containsKey('enabled')) 'enabled': body['enabled']
      };
      return fact;
    }
    if (p == '/v1/conversations/$cid') {
      if (method == 'DELETE') {
        if (failDelete) throw const ApiFailure(409);
        return {};
      }
      if (method == 'PATCH') {
        return {
          ...row(revision: 3),
          if (body!.containsKey('title')) 'title': body['title'],
          if (body.containsKey('pinned')) 'pinned': body['pinned']
        };
      }
      return row(engine: engine);
    }
    if (p.endsWith('/terminals') && method == 'POST') return handle();
    if (p.endsWith('/output')) {
      return {
        'version': 1,
        'handle': handle(),
        'output': 'real fixture output',
        'next_offset': 19,
        'truncated': false
      };
    }
    if (p.endsWith('/input')) return {};
    if (p.endsWith('/size') || p.endsWith('/interrupt')) return handle();
    if (p.contains('/terminals/') && method == 'DELETE') {
      return handle(status: 'exited');
    }
    if (p.endsWith('/checkpoints')) {
      return {
        'version': 1,
        'checkpoints': [
          {
            'version': 1,
            'id': tid,
            'label': 'Before edit',
            'created_at': date,
            'revision': 1,
            'workspace_id': 'root-a'
          }
        ]
      };
    }
    if (p.endsWith('/preview')) {
      return {
        'version': 1,
        'checkpoint_id': tid,
        'expected_revision': 1,
        'preview_hash': 'f' * 64,
        'changes': [
          {'path': 'readme.md'}
        ],
        'unsupported': []
      };
    }
    if (p.endsWith('/restore')) {
      return {
        'version': 1,
        'checkpoint_id': tid,
        'safety_checkpoint_id': cid,
        'status': 'restored'
      };
    }
    throw StateError('Unexpected fixture request $method $path');
  }
}

Future<void> ready(BasicsController c) async {
  for (var i = 0; i < 100 && c.busy; i++) {
    await Future<void>.delayed(Duration.zero);
  }
  expect(c.busy, false);
}

({BasicsController controller, DesktopSession session, WireFixture wire}) setup(
    {bool advanced = false}) {
  final wire = WireFixture()..advanced = advanced;
  final session = DesktopSession(DemoApi())
    ..connected = true
    ..session = Session('s1');
  final controller =
      BasicsController(session, client: AgentBasicsClient(wire.request));
  return (controller: controller, session: session, wire: wire);
}

void main() {
  test('history browse/search/pagination cannot create or resume runtime',
      () async {
    final f = setup();
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    await f.controller.open(cid);
    await f.controller.older();
    expect(f.controller.events.map((e) => e.sequence), [1, 5, 6]);
    await f.controller.more();
    expect(f.controller.conversations.length, 1);
    expect(f.controller.cursor, null);
    await f.controller.search('needle');
    expect(
        f.controller.matches.single.snippet, contains('complete transcript'));
    expect(f.wire.requests.every((r) => r.method == 'GET'), true);
    expect(f.session.session!.id, 's1');
  });
  test(
      'mode stays acknowledged until success; revision conflict is visible and not retried',
      () async {
    final f = setup();
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    expect(f.controller.mode!.mode, AccessMode.ask);
    f.wire.failMode = true;
    await f.controller.setMode(AccessMode.full);
    expect(f.controller.mode, isNull);
    expect(f.controller.error, contains('State changed'));
    expect(f.wire.requests.where((r) => r.method == 'PUT').length, 1);
    f.wire.failMode = false;
    await f.controller.refresh();
    await f.controller.setMode(AccessMode.auto);
    expect(f.controller.mode!.mode, AccessMode.auto);
    expect(f.controller.modeDescription, contains('root-a'));
  });
  test('late mode receipt cannot cross a session switch', () async {
    final f = setup();
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    final completer = Completer<Json>();
    f.wire.delayedMode = completer;
    final pending = f.controller.setMode(AccessMode.full);
    for (var i = 0;
        i < 20 && !f.wire.requests.any((r) => r.method == 'PUT');
        i++) {
      await Future<void>.delayed(Duration.zero);
    }
    f.session.session = Session('s2');
    f.session.notifyListeners();
    await ready(f.controller);
    completer
        .complete({...f.wire.modeData('s1'), 'mode': 'full', 'revision': 1});
    await pending;
    expect(f.controller.mode!.sessionId, 's2');
    expect(f.controller.mode!.mode, AccessMode.ask);
  });
  test('service restart blocks a write before mutation and requires refresh',
      () async {
    final f = setup();
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    await f.controller.open(cid);
    f.wire.instance = 'service-2';
    await f.controller.update(title: 'stale');
    expect(f.wire.requests.where((r) => r.method == 'PATCH'), isEmpty);
    expect(f.controller.error, contains('service restarted'));
    expect(f.controller.capabilities, null);
  });
  test('failed delete retains transcript and does not claim success', () async {
    final f = setup();
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    await f.controller.open(cid);
    f.wire.failDelete = true;
    await f.controller.deleteSelected();
    expect(f.controller.selected!.id, cid);
    expect(f.controller.events, isNotEmpty);
    expect(f.controller.notice, null);
    expect(f.wire.requests.last.body, {'expected_revision': 2});
  });
  test(
      'Harness remains read-only and advanced capability false sends no request',
      () async {
    final f = setup();
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    f.wire.engine = 'harness';
    await f.controller.refresh();
    await f.controller.open(cid);
    expect(await f.controller.resumeSelected(), null);
    expect(f.controller.error, contains('read-only'));
    expect(f.wire.requests.where((r) => r.path.endsWith('/resume')), isEmpty);
    final n = f.wire.requests.length;
    await f.controller.createTerminal();
    expect(f.wire.requests.length, n);
    expect(f.controller.error, contains('unavailable'));
  });
  test(
      'terminal only creates explicitly, output is isolated and checkpoint restore binds exact preview',
      () async {
    final f = setup(advanced: true);
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    expect(
        f.wire.requests.where((r) => r.path.endsWith('/terminals')), isEmpty);
    await f.controller.createTerminal();
    await f.controller.readTerminal();
    expect(f.controller.terminalText, 'real fixture output');
    await f.controller.input('pwd\n');
    expect(
        f.wire.requests.last.body, {'text': 'pwd\n', 'expected_revision': 1});
    await f.controller.loadCheckpoints();
    await f.controller.previewCheckpoint(f.controller.checkpoints.single);
    final preview = f.controller.preview!;
    await f.controller.restoreCheckpoint(preview);
    expect(f.wire.requests.last.body,
        {'expected_revision': 1, 'preview_hash': 'f' * 64});
    expect(f.controller.preview, null);
    final n = f.wire.requests.length;
    await f.controller.restoreCheckpoint(preview);
    expect(f.wire.requests.length, n);
    expect(f.controller.error, contains('Preview changed'));
    f.session.session = Session('s2');
    f.session.notifyListeners();
    await ready(f.controller);
    expect(f.controller.terminal, null);
    expect(f.controller.terminalText, isEmpty);
    f.session.session = Session('s1');
    f.session.notifyListeners();
    await ready(f.controller);
    expect(f.controller.terminalText, 'real fixture output');
  });
  test('memory CRUD uses exact revisions and rejects cross-project responses',
      () async {
    final f = setup(advanced: true);
    addTearDown(f.controller.dispose);
    addTearDown(f.session.dispose);
    await ready(f.controller);
    await f.controller.open(cid);
    await f.controller.loadMemory();
    expect(f.controller.facts.single.text, 'User-created fact');
    await f.controller.toggleMemory(f.controller.facts.single, false);
    expect(f.controller.facts.single.enabled, false);
    expect(
        f.wire.requests.last.body, {'expected_revision': 0, 'enabled': false});
    await f.controller
        .saveMemory('Edited explicit fact', fact: f.controller.facts.single);
    expect(f.controller.facts.single.text, 'Edited explicit fact');
    await f.controller.loadContext();
    expect(f.controller.contextUsed!.data['method'], 'none');
    f.wire.wrongProjectMemory = true;
    await f.controller.loadMemory();
    expect(f.controller.error, contains('invalid contract'));
    expect(f.controller.facts.single.data['project_id'], pid);
    f.wire.wrongProjectMemory = false;
    await f.controller.deleteMemory(f.controller.facts.single);
    expect(f.controller.facts, isEmpty);
  });
  for (final width in [420.0, 1200.0]) {
    testWidgets(
        'advanced navigation at $width is narrow-safe and never starts a shell',
        (tester) async {
      for (final (family, asset) in [
        ('OdessySans', 'assets/fonts/roboto-regular.ttf'),
        ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf')
      ]) {
        await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
      }
      tester.view.physicalSize = Size(width, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final f = setup();
      await tester.pump();
      final captureKey = GlobalKey();
      await tester.pumpWidget(RepaintBoundary(
          key: captureKey,
          child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: odessyTheme(Brightness.light),
              home: Scaffold(
                  body: Column(children: [
                const MaterialBanner(
                    content:
                        Text('CLIENT FIXTURE · offline service contract data'),
                    actions: [SizedBox.shrink()]),
                Expanded(
                    child: BasicsPage(
                        controller: f.controller, onResume: () async {}))
              ])))));
      await tester.pumpAndSettle();
      expect(find.text('History & tools'), findsOneWidget);
      final captureDir = Platform.environment['ODESSY_06_CAPTURE_DIR'];
      if (captureDir != null) {
        await tester.runAsync(() async {
          final boundary = captureKey.currentContext!.findRenderObject()!
              as RenderRepaintBoundary;
          final image = await boundary.toImage(pixelRatio: 1);
          final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
          await Directory(captureDir).create(recursive: true);
          await File('$captureDir/history-${width.toInt()}.png')
              .writeAsBytes(bytes!.buffer.asUint8List());
          image.dispose();
        });
      }
      await tester.tap(find.text('Memory'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Memory is unavailable'), findsOneWidget);
      await tester.ensureVisible(find.text('Terminal'));
      await tester.tap(find.text('Terminal'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Terminal is unavailable'), findsOneWidget);
      expect(f.wire.requests.where((r) => r.method != 'GET'), isEmpty);
      expect(tester.takeException(), null);
      await tester.pumpWidget(const SizedBox.shrink());
      f.controller.dispose();
      f.session.dispose();
    });
  }
}
