import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

const cid = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    pid = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const date = '2026-11-14T09:00:00Z';
Json row() => {
      'version': 1,
      'id': cid,
      'project_id': pid,
      'engine': 'legacy',
      'profile': 'local',
      'title': 'Saved boat',
      'pinned': false,
      'archived': false,
      'revision': 2,
      'created_at': date,
      'updated_at': date,
      'continuation': 'continuable'
    };
Json cap() => {
      'version': 1,
      'service_instance_id': 'service-1',
      'conversations': true,
      'custom_continuation': true,
      'harness_continuation': false,
      'access_modes': true,
      'context': false,
      'memory': false,
      'retrieval': false,
      'terminal': false,
      'checkpoints': false
    };
Json mode() => {
      'version': 1,
      'session_id': 's1',
      'service_instance_id': 'service-1',
      'mode': 'ask',
      'revision': 0,
      'supported_modes': ['ask', 'auto', 'full'],
      'reviewer': 'policy-v1',
      'scope': {'workspace_id': 'root-a', 'target_id': 'local'},
      'effective': true
    };
void main() {
  test('strict rows reject malformed authority and cannot mutate cached values',
      () {
    for (final v in [
      {...row(), 'revision': true},
      {...row(), 'pinned': 1},
      {...row(), 'engine': 'other'},
      {...row(), 'engine': 'harness', 'continuation': 'continuable'},
      {...row(), 'id': '../../elsewhere'},
      {...row(), 'created_at': 'yesterday'},
      {...row(), 'title': 'x' * 121},
      {...row(), 'unknown': true},
    ]) {
      expect(() => SavedConversation.fromJson(v), throwsFormatException);
    }
    final parsed = SavedConversation.fromJson(row());
    expect(() => parsed.data['title'] = 'changed', throwsUnsupportedError);
    expect(parsed.revision, 2);
  });
  test(
      'capabilities fail closed and modes retain real scope and effective state',
      () {
    expect(BasicsCapabilities.fromJson(cap()).supports('terminal'), false);
    expect(() => BasicsCapabilities.fromJson({...cap(), 'terminal': 'true'}),
        throwsFormatException);
    expect(() => BasicsCapabilities.fromJson({...cap(), 'version': 2}),
        throwsFormatException);
    final parsed = ModeSnapshot.fromJson(mode());
    expect(parsed.scopeLabel, contains('root-a'));
    expect(parsed.supported, AccessMode.values);
    expect(
        () => ModeSnapshot.fromJson({
              ...mode(),
              'supported_modes': ['ask', 'ask']
            }),
        throwsFormatException);
    expect(() => ModeSnapshot.fromJson({...mode(), 'effective': 'yes'}),
        throwsFormatException);
    expect(
        ModeSnapshot.fromJson({
          ...mode(),
          'scope': null,
          'effective': false,
          'supported_modes': []
        }).effective,
        false);
  });
  test('transcript pagination validates event order and immutable payload', () {
    Json event(int n) => {
          'sequence': n,
          'event_id': 'e$n',
          'kind': 'assistant',
          'created_at': date,
          'payload': {'text': 'answer', 'turn_id': 't'}
        };
    final v = {
      'version': 1,
      'conversation_id': cid,
      'messages': [event(1), event(2)],
      'next_before': null
    };
    expect(MessagePageData.fromJson(v).events.length, 2);
    expect(
        () => MessagePageData.fromJson({
              ...v,
              'messages': [event(2), event(1)]
            }),
        throwsFormatException);
    expect(() => MessagePageData.fromJson({...v, 'next_before': false}),
        throwsFormatException);
    expect(() => SavedEvent.fromJson({...event(1), 'kind': 'execute'}),
        throwsFormatException);
  });
  test(
      'advanced wire contracts reject malformed context, terminal and previews',
      () {
    final cp = FileCheckpoint.fromJson({
      'version': 1,
      'id': 'ckpt_$cid',
      'label': 'Actual 05 shape',
      'created_at': date,
      'revision': 0,
      'workspace_id': 'root-a',
      'files': 2,
      'bytes': 120
    });
    expect(cp.id, 'ckpt_$cid');
    expect(() => FileCheckpoint.fromJson({...cp.data, 'bytes': true}),
        throwsFormatException);
    final h = {
      'version': 1,
      'id': cid,
      'session_id': 's1',
      'service_instance_id': 'service-1',
      'revision': 0,
      'status': 'running',
      'exit_code': null
    };
    expect(TerminalHandle.fromJson(h).running, true);
    expect(() => TerminalHandle.fromJson({...h, 'revision': -1}),
        throwsFormatException);
    expect(() => TerminalHandle.fromJson({...h, 'status': 'success'}),
        throwsFormatException);
    expect(
        () => CheckpointPreview.fromJson({
              'version': 1,
              'checkpoint_id': cid,
              'expected_revision': 1,
              'preview_hash': 'unbound',
              'changes': [],
              'unsupported': []
            }),
        throwsFormatException);
    expect(
        () => UsedContext.fromJson({
              'version': 1,
              'conversation_id': cid,
              'revision': 0,
              'summary': null,
              'memory_ids': [],
              'citations': [],
              'chars_used': 10,
              'budget_chars': 5,
              'method': 'extractive-v1'
            }),
        throwsFormatException);
  });
  test(
      'LocalApi reuses bearer transport and exact revision-bound routes; errors never retry',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final calls = <String>[], bodies = <Json>[];
    var fail = false;
    server.listen((request) async {
      calls.add('${request.method} ${request.uri.path}');
      expect(request.headers.value(HttpHeaders.authorizationHeader),
          'Bearer fixture-only');
      final text = await utf8.decoder.bind(request).join();
      final body =
          text.isEmpty ? <String, dynamic>{} : jsonDecode(text) as Json;
      bodies.add(body);
      if (fail) {
        request.response.statusCode = 409;
        await request.response.close();
        return;
      }
      final path = request.uri.path;
      final Object response;
      if (path.endsWith('capabilities')) {
        response = cap();
      } else if (path.endsWith('access-mode')) {
        response = {
          ...mode(),
          if (request.method == 'PUT') ...{'mode': body['mode'], 'revision': 1}
        };
      } else if (path.endsWith('/resume')) {
        response = {
          'version': 1,
          'conversation_id': cid,
          'session_id': 'new-session',
          'service_instance_id': 'service-1',
          'continuation': 'continuable'
        };
      } else if (path.endsWith('/bind-workspace')) {
        response = {...row(), 'revision': 3};
      } else if (path.endsWith('/export')) {
        response = {'version': 1, 'conversation': row(), 'events': []};
      } else if (path.endsWith('/search')) {
        expect(request.uri.queryParameters['query'], 'a & b?');
        response = {'version': 1, 'matches': [], 'next_cursor': null};
      } else if (request.method == 'PATCH') {
        response = {
          ...row(),
          ...Map<String, dynamic>.from(body)..remove('expected_revision'),
          'revision': 3
        };
      } else if (request.method == 'DELETE') {
        request.response.statusCode = 204;
        await request.response.close();
        return;
      } else {
        response = row();
      }
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode(response));
      await request.response.close();
    });
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture-only');
    try {
      final client = api.agentBasics;
      expect((await client.capabilities()).serviceInstance, 'service-1');
      final m = await client.mode('s1');
      expect((await client.setMode(m, AccessMode.full)).mode, AccessMode.full);
      expect(bodies.last, {
        'mode': 'full',
        'expected_revision': 0,
        'service_instance_id': 'service-1'
      });
      final r = SavedConversation.fromJson(row());
      await client.update(r, title: 'Renamed');
      expect(bodies.last, {'expected_revision': 2, 'title': 'Renamed'});
      expect((await client.resume(r)).sessionId, 'new-session');
      expect(bodies.last, {'expected_revision': 2});
      await client.bind(r, 's1', 'service-1');
      expect(bodies.last, {
        'expected_revision': 2,
        'session_id': 's1',
        'service_instance_id': 'service-1'
      });
      await client.export(cid);
      await client.search('a & b?');
      await client.delete(r);
      expect(bodies.last, {'expected_revision': 2});
      final count = calls.length;
      fail = true;
      await expectLater(client.update(r, pinned: true),
          throwsA(isA<ApiFailure>().having((e) => e.status, 'status', 409)));
      expect(calls.length, count + 1);
      expect(calls.where((s) => s.startsWith('POST')), hasLength(2));
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
