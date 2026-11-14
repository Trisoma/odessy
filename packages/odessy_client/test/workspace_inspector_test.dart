import 'dart:io';
import 'dart:convert';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

final hash = 'a' * 64;
Json context(String sid) => {
      'service_instance_id': 'instance',
      'session_id': sid,
      'workspace_id': 'root'
    };
Json file(String sid, {String path = 'a.txt', String text = 'hello'}) => {
      ...context(sid),
      'path': path,
      'text': text,
      'sha256': hash,
      'truncated': false
    };

void main() {
  test(
      'live attachment admission sends identities without client file contents',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    Json? received;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/sessions/s/turns');
      received = jsonDecode(await utf8.decoder.bind(request).join()) as Json;
      request.response.statusCode = 202;
      request.response.write('{}');
      await request.response.close();
    });
    try {
      await api.sendWithAttachments(
          's', 'question', [WorkspaceTextFile.fromJson(file('s'))]);
      expect(received!['text'], 'question');
      expect((received!['attachments'] as List).single, {
        'path': 'a.txt',
        'sha256': hash,
        'service_instance_id': 'instance',
        'workspace_id': 'root',
      });
      expect(jsonEncode(received), isNot(contains('hello')));
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('adapter encodes relative paths and rejects foreign response identities',
      () async {
    final paths = <String>[];
    final client = WorkspaceInspectorClient((path) async {
      paths.add(path);
      return file('sid', path: 'folder/a b.txt');
    });
    final response = await client.readWorkspaceFile('sid', 'folder/a b.txt');
    expect(response.path, 'folder/a b.txt');
    expect(Uri.parse(paths.single).queryParameters['path'], 'folder/a b.txt');
    await expectLater(client.readWorkspaceFile('foreign', 'folder/a b.txt'),
        throwsFormatException);
    for (final path in [
      '../outside',
      '..\\outside',
      '/etc/passwd',
      'C:\\x',
      '\\\\host\\share'
    ]) {
      await expectLater(
          client.readWorkspaceFile('sid', path), throwsFormatException);
    }
    expect(paths.length, 2); // Invalid paths never reach transport.
  });

  test('strict bounds and immutable directory parser', () {
    final data = {
      ...context('sid'),
      'path': '.',
      'entries': [
        {'name': 'a.txt', 'path': 'a.txt', 'kind': 'file'}
      ],
      'truncated': false
    };
    final tree = WorkspaceTree.fromJson(data);
    expect(() => tree.entries.clear(), throwsUnsupportedError);
    for (final bad in [
      {
        ...data,
        'entries': [
          {'name': 'a', 'path': '../a', 'kind': 'file'}
        ]
      },
      {
        ...data,
        'entries': [
          {'name': 'a', 'path': 'a', 'kind': 'link'}
        ]
      },
      {
        ...data,
        'entries': List.generate(
            201, (_) => {'name': 'a', 'path': 'a', 'kind': 'file'})
      },
      {
        ...data,
        'entries': [
          {'name': 'a', 'path': 'a', 'kind': 'file'},
          {'name': 'a', 'path': 'a', 'kind': 'file'}
        ]
      },
    ]) {
      expect(() => WorkspaceTree.fromJson(bad), throwsFormatException);
    }
    for (final bad in [
      {...file('sid'), 'sha256': 'bad'},
      {...file('sid'), 'truncated': true},
      {...file('sid'), 'text': 'binary\u0000'},
      {...file('sid'), 'text': 'é' * (256 * 1024)}
    ]) {
      expect(() => WorkspaceTextFile.fromJson(bad), throwsFormatException);
    }
  });

  test('attachments revalidate exact hash, bytes, context and combined budget',
      () async {
    Json fresh = file('sid');
    final client = WorkspaceInspectorClient((_) async => fresh);
    final descriptor = WorkspaceDescriptor.fromJson({
      ...context('sid'),
      'label': 'Root',
      'capabilities': {
        'read_files': true,
        'proposed_changes': false,
        'recorded_output': true
      }
    });
    final attachments = [
      WorkspaceAttachment(WorkspaceTextFile.fromJson(file('sid')))
    ];
    expect(await revalidateAttachments(client, descriptor, attachments),
        contains('hello'));
    fresh = {...fresh, 'sha256': 'b' * 64};
    await expectLater(revalidateAttachments(client, descriptor, attachments),
        throwsStateError);
    fresh = {...file('sid'), 'workspace_id': 'other'};
    await expectLater(revalidateAttachments(client, descriptor, attachments),
        throwsStateError);
    fresh = file('sid', text: 'x' * 32768);
    await expectLater(
        revalidateAttachments(client, descriptor,
            [WorkspaceAttachment(WorkspaceTextFile.fromJson(fresh))]),
        throwsStateError);
  });

  test(
      'actual sanitized result observations are ordered and session-owned; progress is not output',
      () {
    SessionEvent result(int id, String call, String status, String stdout) =>
        SessionEvent(id, 'result', {
          'tool_observations': [
            {
              'call_id': call,
              'request': {'name': 'terminal.run'},
              'result': {
                'status': status,
                'stdout': stdout,
                'stderr': '',
                'exit_code': status == 'ok' ? 0 : 2
              }
            }
          ]
        });
    final snapshot = Session('sid', events: [
      result(3, 'b', 'failed', 'second'),
      SessionEvent(2, 'tool', {'name': 'terminal.run', 'title': 'not stdout'}),
      result(1, 'a', 'ok', 'first'),
      result(3, 'b', 'failed', 'second')
    ]);
    final output = recordedSessionOutputs(snapshot);
    expect(output.map((o) => o.stdout), ['first', 'second']);
    expect(output.map((o) => o.sessionId).toSet(), {'sid'});
    expect(output.last.state, 'error');
    expect(
        recordedSessionOutputs(Session('harness', events: [
          SessionEvent(1, 'tool', {'title': 'running'})
        ])),
        isEmpty);
    final bounded = recordedSessionOutputs(
        Session('sid', events: [result(4, 'c', 'ok', 'x' * 70000)])).single;
    expect(bounded.stdout.length, 65536);
    expect(bounded.truncated, isTrue);
  });

  test('adapter works over one authenticated transport without mutations',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final requests = <String>[];
    server.listen((request) async {
      requests.add(request.method);
      expect(request.headers.value('Authorization'), 'Bearer fixture-only');
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode(file('sid')));
      await request.response.close();
    });
    final http = HttpClient();
    try {
      final api = WorkspaceInspectorClient((path) async {
        final request = await http
            .getUrl(Uri.parse('http://127.0.0.1:${server.port}$path'));
        request.headers.set('Authorization', 'Bearer fixture-only');
        final response = await request.close();
        return jsonDecode(await utf8.decoder.bind(response).join()) as Json;
      });
      expect((await api.readWorkspaceFile('sid', 'a.txt')).text, 'hello');
      expect(requests, ['GET']);
    } finally {
      http.close(force: true);
      await server.close(force: true);
    }
  });
}
