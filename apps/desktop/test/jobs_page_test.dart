import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/jobs/controllers/jobs_controller.dart';
import 'package:odessy_desktop/features/jobs/presentation/jobs_page.dart';
import 'conversation_presentation_test.dart';

class JobsFixtureApi extends ConversationFixtureApi implements JobsApi {
  final id = 'a' * 32;
  Completer<Json>? submission;
  int submits = 0, cancellations = 0;
  bool failCancellation = false;
  @override
  Future<Json> listJobs(String sid, {String? after, int limit = 50}) async => {
        'jobs': [
          {
            'job_id': id,
            'participant': 'coder',
            'mode': 'advice',
            'state': 'running',
            'remote_state_unknown': false
          }
        ],
        'next_cursor': null
      };
  @override
  Future<Json> readJob(String sid, String jobId) async =>
      {'job_id': jobId, 'state': 'running'};
  @override
  Future<Json> cancelJob(String sid, String jobId) async {
    cancellations++;
    if (failCancellation) throw StateError('connection lost');
    return {'job_id': jobId, 'state': 'cancelled'};
  }

  @override
  Future<Json> submitJob(String sid, Json request) {
    submits++;
    return submission?.future ?? Future.value({'job_id': id});
  }
}

void main() {
  testWidgets('unconfirmed cancellation requires a fresh job status',
      (tester) async {
    final api = JobsFixtureApi()..failCancellation = true;
    final session = DesktopSession(api);
    await session.start();
    final jobs = JobsController(session);
    addTearDown(jobs.dispose);
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: JobsPage(controller: jobs))));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('coder · advice'));
    await tester.tap(find.text('coder · advice'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Cancel job'));
    await tester.tap(find.text('Cancel job'));
    await tester.pumpAndSettle();
    expect(jobs.selectedCancellationUncertain, isTrue);
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, 'Cancel job'))
            .onPressed,
        isNull);
    await jobs.cancel(api.id);
    expect(api.cancellations, 1);
    await tester.ensureVisible(find.text('Reload job status'));
    await tester.tap(find.text('Reload job status'));
    await tester.pumpAndSettle();
    expect(jobs.selectedCancellationUncertain, isFalse);
    api.failCancellation = false;
    await tester.ensureVisible(find.text('Cancel job'));
    await tester.tap(find.text('Cancel job'));
    await tester.pumpAndSettle();
    expect(api.cancellations, 2);
  });
  testWidgets('job form requires a participant and task before submission',
      (tester) async {
    final api = JobsFixtureApi();
    final session = DesktopSession(api);
    await session.start();
    final jobs = JobsController(session);
    addTearDown(jobs.dispose);
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: JobsPage(controller: jobs))));
    await tester.pumpAndSettle();
    final submit = find.widgetWithText(FilledButton, 'Request job');
    expect(tester.widget<FilledButton>(submit).onPressed, isNull);
    await tester.enterText(
        find.widgetWithText(TextField, 'Participant profile'), 'coder');
    await tester.enterText(find.widgetWithText(TextField, 'Task'), 'Inspect');
    await tester.pump();
    expect(tester.widget<FilledButton>(submit).onPressed, isNotNull);
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(api.submits, 1);
  });

  testWidgets('jobs page loads details and explicitly cancels', (tester) async {
    final api = JobsFixtureApi();
    final session = DesktopSession(api);
    await session.start();
    final jobs = JobsController(session);
    addTearDown(jobs.dispose);
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: JobsPage(controller: jobs))));
    await tester.pumpAndSettle();
    expect(find.text('coder · advice'), findsOneWidget);
    await tester.ensureVisible(find.text('coder · advice'));
    await tester.tap(find.text('coder · advice'));
    await tester.pumpAndSettle();
    expect(find.text('Job details'), findsOneWidget);
    await tester.ensureVisible(find.text('Cancel job'));
    await tester.tap(find.text('Cancel job'));
    await tester.pumpAndSettle();
    expect(api.cancellations, 1);
    expect(find.text('Cancel job'), findsNothing);
  });

  testWidgets('coding CLI usage stays separate and unknown values stay unknown',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
            body: JobUsage(usage: {
      'source': 'coding_cli',
      'input_tokens': 100,
      'cached_input_tokens': null,
      'output_tokens': 30,
      'cost': 0.5,
      'currency': 'USD'
    }))));
    expect(find.text('Input tokens: 100'), findsOneWidget);
    expect(find.text('Cached input tokens: Not reported'), findsOneWidget);
    expect(find.text('Reported cost: 0.5 USD'), findsOneWidget);
    expect(find.textContaining('Separate from the API ledger'), findsOneWidget);
    await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
            body: JobUsage(usage: {
      'source': 'coding_cli',
      'input_tokens': -1,
      'cost': null,
      'currency': 'USD'
    }))));
    expect(find.text('Input tokens: Not reported'), findsOneWidget);
    expect(find.text('Reported cost: Not reported'), findsOneWidget);
  });

  test(
      'in-flight submission does not block approval and becomes uncertain on replacement',
      () async {
    final api = JobsFixtureApi()..submission = Completer<Json>();
    api.pending = [interactionFixture()];
    final session = DesktopSession(api);
    await session.start();
    final jobs = JobsController(session);
    addTearDown(jobs.dispose);
    addTearDown(session.dispose);
    final pending =
        jobs.submit(participant: 'coder', task: 'inspect', mode: 'advice');
    expect(jobs.submitting, isTrue);
    final request = session.session!.pending.single;
    await session.resolve(request, approved: false);
    expect(api.approvals, 1);
    await session.replace(DemoApi(delay: Duration.zero));
    expect(jobs.submissionUncertain, isTrue);
    api.submission!.complete({'job_id': api.id});
    await pending;
    expect(jobs.submissionUncertain, isTrue);
    expect(api.submits, 1);
    expect(jobs.jobs, isEmpty);
  });
}
