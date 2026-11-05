import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/handoff/controllers/handoff_controller.dart';
import 'package:odessy_desktop/features/handoff/presentation/handoff_page.dart';
import 'conversation_presentation_test.dart';

final runId = 'c' * 32, firstSha = 'd' * 64, secondSha = 'e' * 64;

Json runFixture({String state = 'awaiting_handoff', String? sha}) {
  final offered = state == 'awaiting_handoff';
  return {
    'type': 'collaboration.run',
    'run_id': runId,
    'flow': 'document_to_code',
    'state': state,
    'worktree': '/repo-odessy-run-cccc',
    'error': state == 'needs_attention' ? 'the worktree changed' : null,
    'steps': [
      {
        'step_id': 's1',
        'participant': 'claude',
        'mode': 'advice',
        'state': state == 'running_step_1' ? 'running' : 'completed',
        'job_id': 'a' * 32,
        'progress': ['Reading the repository'],
        'changed_files': [],
      },
      {
        'step_id': 's2',
        'participant': 'codex',
        'mode': 'patch',
        'state': state == 'running_step_2' ? 'running' : 'pending',
        'job_id': state == 'running_step_2' ? 'b' * 32 : null,
        'progress': [],
        'changed_files': [],
      }
    ],
    'transfers': [
      {
        'transfer_id': 't1',
        'from': 's1',
        'to': 's2',
        'state': offered
            ? 'pending'
            : state == 'running_step_2'
                ? 'delivered'
                : 'waiting',
        if (state == 'running_step_2') 'path': '.odessy-handoff/t1.md',
        'artifact': offered || state == 'running_step_2'
            ? {
                'kind': 'document',
                'sha256': sha ?? firstSha,
                'bytes': 12,
                'preview': 'The plan: …',
                'changed_files': [],
              }
            : null,
      }
    ],
  };
}

class HandoffFixtureApi extends ConversationFixtureApi
    implements HandoffRunsApi {
  int _event = 0;
  final continued = <String>[];
  int starts = 0, cancels = 0, reads = 0;
  Object? startFailure, continueFailure;
  Json Function()? readResult;
  Completer<Json>? read;
  String artifactSha = firstSha;

  void emitRun(Json run) => activity = [
        ...activity,
        SessionEvent(++_event, 'run', run),
      ];

  @override
  Future<Json> startRun(String sid, Json request) async {
    starts++;
    if (startFailure != null) throw startFailure!;
    return parseRunSnapshot(runFixture(state: 'running_step_1'));
  }

  @override
  Future<Json> readRun(String sid, String id) async {
    reads++;
    if (read != null) return read!.future;
    return parseRunSnapshot(readResult?.call() ?? runFixture());
  }

  @override
  Future<Json> readRunArtifact(String sid, String id) async =>
      parseRunArtifact({
        'kind': 'document',
        'sha256': artifactSha,
        'bytes': 12,
        'preview': 'The plan: …',
        'changed_files': [],
        'text': 'The plan: add two numbers and return the sum.',
      });

  @override
  Future<Json> continueRun(String sid, String id, String sha) async {
    continued.add(sha);
    if (continueFailure != null) throw continueFailure!;
    return parseRunSnapshot(runFixture(state: 'running_step_2', sha: sha));
  }

  @override
  Future<Json> cancelRun(String sid, String id) async {
    cancels++;
    return parseRunSnapshot(runFixture(state: 'cancelled'));
  }
}

Future<(HandoffFixtureApi, DesktopSession, HandoffController)> connect(
    {List<Json> runs = const []}) async {
  final api = HandoffFixtureApi();
  for (final run in runs) {
    api.emitRun(run);
  }
  final session = DesktopSession(api);
  await session.start();
  final controller = HandoffController(session);
  addTearDown(controller.dispose);
  addTearDown(session.dispose);
  return (api, session, controller);
}

Future<void> show(WidgetTester tester, HandoffController controller,
    {Size size = const Size(1280, 900)}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: HandoffPage(controller: controller))));
  await settle(tester);
}

/// Running steps animate a progress bar, so the frame never settles.
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  testWidgets('a run from session events shows both agents and the transfer',
      (tester) async {
    final (_, _, handoffs) = await connect(runs: [runFixture()]);
    await show(tester, handoffs);
    expect(find.text('Document → code · Waiting for your decision'),
        findsOneWidget);
    expect(
        find.bySemanticsLabel(RegExp('Step 1, claude, read-only, Completed')),
        findsOneWidget);
    expect(
        find.bySemanticsLabel(
            RegExp('Step 2, codex, writes code, Not started')),
        findsOneWidget);
    expect(
        find.bySemanticsLabel(RegExp(
            'Document from step 1 to step 2, Waiting for your decision')),
        findsOneWidget);
    expect(find.text('SHA-256 $firstSha'), findsOneWidget);
    expect(find.text('Hand over to codex'), findsOneWidget);
    expect(
        find.textContaining('nothing is merged automatically'), findsOneWidget);
  });

  testWidgets('handing over sends the hash on screen, after newer events',
      (tester) async {
    final (api, session, handoffs) = await connect(runs: [runFixture()]);
    await show(tester, handoffs);
    api.emitRun(runFixture(sha: secondSha));
    await session.refresh();
    await settle(tester);
    expect(find.text('SHA-256 $secondSha'), findsOneWidget);
    await tester.ensureVisible(find.text('Hand over to codex'));
    await tester.tap(find.text('Hand over to codex'));
    await settle(tester);
    expect(api.continued, [secondSha]);
    expect(find.text('Document → code · Running'), findsOneWidget);
    expect(find.text('Hand over to codex'), findsNothing);
    expect(find.textContaining('.odessy-handoff/t1.md'), findsOneWidget);
  });

  testWidgets('the full output is dropped once a different one is offered',
      (tester) async {
    final (api, session, handoffs) = await connect(runs: [runFixture()]);
    await show(tester, handoffs);
    await tester.ensureVisible(find.text('Show full output'));
    await tester.tap(find.text('Show full output'));
    await settle(tester);
    expect(find.textContaining('add two numbers'), findsOneWidget);
    api.emitRun(runFixture(sha: secondSha));
    await session.refresh();
    await settle(tester);
    expect(handoffs.artifact, isNull);
    expect(find.textContaining('add two numbers'), findsNothing);
    // A full output for an older hash is never shown as the current offer.
    await handoffs.loadArtifact();
    expect(handoffs.artifact, isNull);
    expect(handoffs.error, contains('changed while loading'));
  });

  testWidgets('an unconfirmed handoff blocks a second one until reloaded',
      (tester) async {
    final (api, _, handoffs) = await connect(runs: [runFixture()]);
    api.continueFailure = StateError('connection lost');
    await show(tester, handoffs);
    await tester.ensureVisible(find.text('Hand over to codex'));
    await tester.tap(find.text('Hand over to codex'));
    await settle(tester);
    expect(handoffs.continueUncertain, isTrue);
    expect(
        tester
            .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'Hand over to codex'))
            .onPressed,
        isNull);
    await handoffs.handOver();
    expect(api.continued, hasLength(1));
    await tester.tap(find.byTooltip('Reload run'));
    await settle(tester);
    expect(handoffs.continueUncertain, isFalse);
  });

  testWidgets('a declined handoff is reported, not treated as uncertain',
      (tester) async {
    final (api, _, handoffs) = await connect(runs: [runFixture()]);
    api.continueFailure = const ApiFailure(403);
    await show(tester, handoffs);
    await handoffs.handOver();
    await settle(tester);
    expect(handoffs.continueUncertain, isFalse);
    expect(find.text('The service declined this request.'), findsOneWidget);
  });

  testWidgets('start needs every field and an unknown outcome blocks repeats',
      (tester) async {
    final (api, _, handoffs) = await connect();
    api.startFailure = TimeoutException('no answer');
    await show(tester, handoffs);
    final submit = find.widgetWithText(FilledButton, 'Request handoff');
    expect(tester.widget<FilledButton>(submit).onPressed, isNull);
    for (final (label, text) in [
      ('Step 1 participant', 'claude'),
      ('Step 1 task', 'Write a design'),
      ('Step 2 participant', 'codex'),
    ]) {
      await tester.enterText(find.widgetWithText(TextField, label), text);
    }
    await tester.pump();
    expect(tester.widget<FilledButton>(submit).onPressed, isNull);
    await tester.enterText(
        find.widgetWithText(TextField, 'Step 2 task'), 'Implement it');
    await tester.pump();
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await settle(tester);
    expect(api.starts, 1);
    expect(handoffs.startUncertain, isTrue);
    expect(tester.widget<FilledButton>(submit).onPressed, isNull);
    await tester.ensureVisible(find.text('I have checked the previous start'));
    await tester.tap(find.text('I have checked the previous start'));
    api.startFailure = null;
    await settle(tester);
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await settle(tester);
    expect(api.starts, 2);
    expect(find.text('Document → code · Running'), findsOneWidget);
  });

  test('a reply older than an event does not replace it', () async {
    final (api, session, handoffs) =
        await connect(runs: [runFixture(state: 'running_step_1')]);
    api.read = Completer<Json>();
    final reload = handoffs.reload();
    api.emitRun(runFixture());
    await session.refresh();
    api.read!.complete(parseRunSnapshot(runFixture(state: 'running_step_1')));
    await reload;
    expect(handoffs.selected!['state'], 'awaiting_handoff');
  });

  test('malformed events are skipped and a new session starts empty', () async {
    final (api, session, handoffs) = await connect(runs: [runFixture()]);
    api.emitRun({...runFixture(state: 'completed'), 'steps': []});
    await session.refresh();
    expect(handoffs.selected!['state'], 'awaiting_handoff');
    await session.replace(DemoApi(delay: Duration.zero));
    expect(handoffs.runs, isEmpty);
    expect(handoffs.available, isFalse);
  });

  testWidgets('cancel is offered only while the run can still change',
      (tester) async {
    final (api, _, handoffs) =
        await connect(runs: [runFixture(state: 'running_step_1')]);
    await show(tester, handoffs);
    await tester.ensureVisible(find.text('Cancel run'));
    await tester.tap(find.text('Cancel run'));
    await settle(tester);
    expect(api.cancels, 1);
    expect(find.text('Cancel run'), findsNothing);
    expect(find.text('Document → code · Cancelled'), findsOneWidget);
  });

  testWidgets('narrow windows stack the flow vertically without overflow',
      (tester) async {
    final (_, _, handoffs) =
        await connect(runs: [runFixture(state: 'needs_attention')]);
    await show(tester, handoffs, size: const Size(420, 900));
    expect(tester.takeException(), isNull);
    expect(find.byIcon(Icons.arrow_downward), findsOneWidget);
    expect(
        find.textContaining('Stopped: the worktree changed'), findsOneWidget);
    expect(find.text('Cancel run'), findsNothing);
  });

  testWidgets('events recorded from the real service draw the whole run',
      (tester) async {
    // Written by tests/test_collaboration_runs.py from a real RunManager.
    final recorded = (jsonDecode(File('test/fixtures/handoff_run_events.json')
            .readAsStringSync()) as List)
        .cast<Json>();
    final (api, session, handoffs) = await connect(runs: [recorded.first]);
    await show(tester, handoffs);
    expect(find.text('Document → code · Waiting for your decision'),
        findsOneWidget);
    expect(find.text('Hand over to coder'), findsOneWidget);
    expect(find.text('Input tokens: Not reported'), findsOneWidget);
    api.emitRun(recorded.last);
    await session.refresh();
    await settle(tester);
    expect(find.text('Document → code · Completed'), findsOneWidget);
    expect(find.textContaining('Changed: app.py'), findsOneWidget);
    expect(find.text('Hand over to coder'), findsNothing);
    expect(find.text('Cancel run'), findsNothing);
  });

  testWidgets('connections without handoff support say so', (tester) async {
    final session = DesktopSession(DemoApi(delay: Duration.zero));
    await session.start();
    final handoffs = HandoffController(session);
    addTearDown(handoffs.dispose);
    addTearDown(session.dispose);
    await show(tester, handoffs);
    expect(find.text('Handoffs are unavailable for this connection.'),
        findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'Request handoff'))
            .onPressed,
        isNull);
  });
}
