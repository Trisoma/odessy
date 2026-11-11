import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/installed_manage_controller.dart';

/// A maintenance helper whose mutating request stays open until the test ends it.
class BlockingBackend implements SetupBackend {
  final Completer<SetupReply> pending = Completer<SetupReply>();
  final List<SetupRequest> requests = <SetupRequest>[];
  bool cancelled = false;

  @override
  Future<SetupReply> execute(SetupRequest request) {
    requests.add(request);
    return pending.future;
  }

  @override
  Future<void> cancel() async => cancelled = true;

  @override
  Future<void> close() async {}
}

/// A second, read-only helper that reports the bounded registration counters.
class ProgressBackend implements SetupBackend {
  int statusReads = 0;

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    expect(request.parameters['action'], 'package-status');
    statusReads++;
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'registration_progress': {
          'status': 'running',
          'stage': 'cache',
          'files_done': 2,
          'files_total': 4,
          'bytes_done': 20,
          'bytes_total': 40,
        },
      },
    }, request.id);
  }

  @override
  Future<void> cancel() async {}

  @override
  Future<void> close() async {}
}

void main() {
  test('finished registration counters do not describe later operations', () {
    final c = InstalledManageController(BlockingBackend());
    addTearDown(c.dispose);
    for (final state in ['completed', 'interrupted', 'cancelled']) {
      c.registrationProgress = {
        'status': state,
        'files_done': 4,
        'files_total': 4
      };
      expect(c.progress, isNull);
      expect(c.progressLabel, isEmpty);
    }
  });

  test('registration reports bounded copy progress and can be cancelled',
      () async {
    final backend = BlockingBackend();
    final progress = ProgressBackend();
    final c = InstalledManageController(backend,
        progressBackend: progress, spec: {'root': 'C:/App'});
    addTearDown(c.dispose);

    unawaited(c.register());
    await Future<void>.delayed(const Duration(milliseconds: 700));
    expect(backend.requests.single.parameters['action'], 'package-register');
    expect(progress.statusReads, greaterThan(0));
    expect(c.busy, isTrue);
    expect(c.progress, 0.5);
    expect(c.progressLabel, contains('2 of 4 files'));

    await c.cancel();
    expect(c.busy, isTrue);
    expect(backend.cancelled, isTrue);
    expect(c.notice, contains('Cancellation requested'));
    // Stay busy until the helper reports its actual cancellation outcome.
    backend.pending.complete(SetupReply.fromJson({
      'id': backend.requests.single.id,
      'status': 'error',
      'reason': 'registration was cancelled',
      'code': 'cancelled',
    }, backend.requests.single.id));
    await Future<void>.delayed(Duration.zero);
    expect(c.busy, isFalse);
    expect(c.error, contains('cancelled'));
  });

  test('a single helper is not polled for its own progress', () async {
    final backend = BlockingBackend();
    final c = InstalledManageController(backend, spec: {'root': 'C:/App'});
    addTearDown(c.dispose);
    unawaited(c.register());
    await Future<void>.delayed(const Duration(milliseconds: 700));
    expect(c.progress, isNull);
    backend.pending.complete(SetupReply.fromJson({
      'id': backend.requests.single.id,
      'status': 'ok',
      'result': {'registered': true},
    }, backend.requests.single.id));
    await c.cancel();
  });
}
