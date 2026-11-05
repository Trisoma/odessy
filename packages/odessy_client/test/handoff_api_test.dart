import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

final runId = 'c' * 32, sha = 'd' * 64;

Map<String, dynamic> runFixture(
        {String state = 'awaiting_handoff', Object? artifact = 'default'}) =>
    {
      'run_id': runId,
      'flow': 'code_to_review',
      'state': state,
      'workspace': '/repo',
      'worktree': '/repo-odessy-run',
      'error': null,
      'steps': [
        {
          'step_id': 's1',
          'participant': 'codex',
          'mode': 'patch',
          'task': 'add',
          'state': 'completed',
          'job_id': 'e' * 32,
          'progress': ['done'],
          'usage': null,
          'changed_files': ['a.py'],
          'result': 'ok',
          'error': null
        },
        {
          'step_id': 's2',
          'participant': 'claude',
          'mode': 'advice',
          'task': 'review',
          'state': 'pending',
          'job_id': null,
          'progress': [],
          'usage': null,
          'changed_files': [],
          'result': null,
          'error': null
        }
      ],
      'transfers': [
        {
          'transfer_id': 't1',
          'from': 's1',
          'to': 's2',
          'state': 'pending',
          'artifact': artifact == 'default'
              ? {
                  'kind': 'diff',
                  'sha256': sha,
                  'bytes': 5,
                  'preview': '+ a',
                  'changed_files': ['a.py']
                }
              : artifact
        }
      ]
    };

void main() {
  test('runs use session routes and send only the displayed hash', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final received = <(String, String, String)>[];
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      final body = await utf8.decoder.bind(request).join();
      received.add((request.method, request.uri.path, body));
      request.response.write(jsonEncode(request.uri.path.endsWith('/artifact')
          ? {
              ...(runFixture()['transfers'] as List).first['artifact'],
              'text': '+ a\n'
            }
          : runFixture()));
      await request.response.close();
    });
    try {
      final request = {
        'flow': 'code_to_review',
        'from_participant': 'codex',
        'from_task': 'add',
        'to_participant': 'claude',
        'to_task': 'review'
      };
      expect((await api.startRun('session-1', request))['run_id'], runId);
      await api.readRun('session-1', runId);
      expect((await api.readRunArtifact('session-1', runId))['text'], '+ a\n');
      await api.continueRun('session-1', runId, sha);
      await api.cancelRun('session-1', runId);
      expect(received.map((r) => '${r.$1} ${r.$2}'), [
        'POST /v1/sessions/session-1/runs',
        'GET /v1/sessions/session-1/runs/$runId',
        'GET /v1/sessions/session-1/runs/$runId/artifact',
        'POST /v1/sessions/session-1/runs/$runId/continue',
        'POST /v1/sessions/session-1/runs/$runId/cancel',
      ]);
      expect(jsonDecode(received[0].$3), request);
      // No approval, channel or risk: the service's approval broker decides.
      expect(jsonDecode(received[3].$3), {'artifact_sha256': sha});
      expect(received[4].$3, isEmpty);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });

  test('malformed identifiers and requests never reach the service', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var requests = 0;
    server.listen((request) async {
      requests++;
      await request.response.close();
    });
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    try {
      expect(() => api.readRun('s', '../x'), throwsFormatException);
      expect(() => api.continueRun('s', runId, 'abc'), throwsFormatException);
      expect(() => api.continueRun('s', runId, sha.toUpperCase()),
          throwsFormatException);
      final valid = {
        'flow': 'document_to_code',
        'from_participant': 'a',
        'from_task': 'b',
        'to_participant': 'c',
        'to_task': 'd'
      };
      for (final bad in [
        {...valid, 'flow': 'anything'},
        {...valid, 'approved': 'true'},
        {...valid}..remove('to_task'),
        {...valid, 'from_task': 3},
      ]) {
        await expectLater(api.startRun('s', bad), throwsFormatException);
      }
      expect(requests, 0);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });

  test('a snapshot that cannot be drawn is rejected', () {
    expect(parseRunSnapshot(runFixture())['state'], 'awaiting_handoff');
    expect(parseRunSnapshot({...runFixture(), 'type': 'collaboration.run'}),
        isA<Map>());
    expect(parseRunSnapshot(runFixture(artifact: null))['run_id'], runId);
    Map<String, dynamic> changed(void Function(Map<String, dynamic>) edit) {
      final run = runFixture();
      edit(run);
      return run;
    }

    for (final bad in <Object?>[
      null,
      'run',
      {...runFixture(), 'run_id': 'short'},
      {...runFixture(), 'flow': 'three_steps'},
      {
        ...runFixture(),
        'steps': (runFixture()['steps'] as List).take(1).toList()
      },
      changed((r) => (r['transfers'] as List).first['to'] = 's9'),
      changed((r) => (r['transfers'] as List).first['from'] = 's2'),
      changed((r) =>
          ((r['transfers'] as List).first['artifact'] as Map)['sha256'] = 'x'),
      changed((r) => (r['steps'] as List).first['progress'] = [1]),
      changed((r) => (r['steps'] as List).last['step_id'] = 's1'),
    ]) {
      expect(() => parseRunSnapshot(bad), throwsFormatException,
          reason: '$bad');
    }
    expect(
        () => parseRunArtifact(
            {'kind': 'diff', 'sha256': sha, 'bytes': 1, 'preview': ''}),
        throwsFormatException);
  });
}
