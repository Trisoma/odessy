import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:odessy_client/odessy_client.dart';
import 'package:test/test.dart';

void main() {
  test('HTTP 503 provides model configuration guidance without server details', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api = LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'secret');
    server.listen((request) async {
      request.response.statusCode = 503;
      request.response.write('{"detail":"private-server-diagnostic"}');
      await request.response.close();
    });
    try {
      for (final operation in [api.createSession, api.profiles]) {
        try {
          await operation();
          fail('Unavailable model must not create a session or profiles');
        } on ApiFailure catch (error) {
          expect(error.status, 503);
          expect(error.toString(), contains('API key'));
          expect(error.toString(), contains('model settings'));
          expect(error.toString(), isNot(contains('private-server-diagnostic')));
        }
      }
    } finally {
      api.close();
      await server.close(force: true);
    }
  });

  test('SSE cancellation does not wait for another server event', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'secret');
    server.listen((request) async {
      request.response.bufferOutput = false;
      request.response.headers.contentType =
          ContentType('text', 'event-stream');
      request.response.write('data: {"id":1,"type":"result","data":{}}\n\n');
      await request.response.flush();
    });
    final seen = Completer<void>();
    final subscription = api.events('s', 0).listen((_) => seen.complete());
    try {
      await seen.future.timeout(const Duration(seconds: 3));
      await subscription.cancel().timeout(const Duration(milliseconds: 500));
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('rejects remote endpoints and credentials in URLs', () {
    for (final url in [
      'http://example.com:8765',
      'http://user:secret@localhost',
      'http://localhost?token=x'
    ]) {
      expect(() => LocalApi(Uri.parse(url), 'secret'), throwsArgumentError);
    }
  });
  test('SSE handles partial chunks, comments and duplicate IDs', () async {
    final chunks = Stream.fromIterable([
      ': keepalive\r\n\r\nid: 1\r\nda',
      'ta: {"id":1,"type":"result","data":{}}\r\n\r\n',
      'data: {"id":1,"type":"result","data":{}}\n\n'
    ]);
    final events = await decodeEvents(chunks).toList();
    expect(events.map((e) => e.id), [1]);
  });
  test('HTTP carries bearer auth and exact approval ID, never a channel',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'secret');
    final received = Completer<Map<String, dynamic>>();
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer secret');
      expect(request.uri.path, '/v1/sessions/s/approvals/a');
      received.complete(jsonDecode(await utf8.decoder.bind(request).join()));
      request.response.write('{"approved":false}');
      await request.response.close();
    });
    await api.approve('s', 'a', false);
    expect(await received.future, {'approved': false});
    api.close();
    await server.close(force: true);
  });
  test('demo stop invalidates delayed generation; selection is next turn only',
      () async {
    final demo = DemoApi(delay: const Duration(milliseconds: 50));
    final session = await demo.createSession();
    await demo.send(session.id, 'hello');
    await demo.select(session.id, 'local');
    expect((await demo.snapshot(session.id)).activeProfile, 'auto');
    await demo.stop(session.id);
    await Future<void>.delayed(const Duration(milliseconds: 80));
    expect(
        (await demo.snapshot(session.id))
            .messages
            .where((e) => e.role == 'assistant'),
        isEmpty);
    demo.close();
  });
}
