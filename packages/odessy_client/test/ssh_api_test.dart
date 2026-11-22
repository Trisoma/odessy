import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('SSH execution selection uses authenticated explicit mutation',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final methods = <String>[];
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/ssh/execution');
      expect(request.uri.query, isEmpty);
      methods.add(request.method);
      final body = await utf8.decoder.bind(request).join();
      if (request.method == 'POST')
        expect(jsonDecode(body), {'confirmed': true});
      request.response.write(jsonEncode({'execution': null}));
      await request.response.close();
    });
    try {
      await api.sshExecutionStatus();
      await api.enableSshExecution();
      await api.disableSshExecution();
      expect(methods, ['GET', 'POST', 'DELETE']);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test(
      'temporary connect sends explicit port/password only in authenticated body',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture-token');
    final bodies = <Object?>[];
    server.listen((request) async {
      expect(request.method, 'POST');
      expect(request.uri.path, '/v1/ssh/connect');
      expect(request.uri.query, isEmpty);
      expect(request.headers.value('authorization'), 'Bearer fixture-token');
      bodies.add(jsonDecode(await utf8.decoder.bind(request).join()));
      request.response.write(jsonEncode({
        'connected': true,
        'target': {'temporary': true}
      }));
      await request.response.close();
    });
    try {
      await api.connectSsh('tester@192.0.2.10',
          port: 2222, password: 'fixture-password');
      await api.connectSsh('tester@192.0.2.10');
      expect(bodies, [
        {
          'target': 'tester@192.0.2.10',
          'port': 2222,
          'password': 'fixture-password'
        },
        {'target': 'tester@192.0.2.10'},
      ]);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test(
      'password requests use authenticated local POST bodies, never URL or retries',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture-token');
    final requests = <String>[];
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture-token');
      expect(request.uri.query, isEmpty);
      requests.add(request.uri.path);
      final body = jsonDecode(await utf8.decoder.bind(request).join());
      expect(
          body, {'alias': 'tester@192.0.2.10', 'password': 'fixture-password'});
      request.response.write(jsonEncode({
        'test': {'ok': true}
      }));
      await request.response.close();
    });
    try {
      await api.testSshPassword('tester@192.0.2.10', 'fixture-password');
      await api.saveSshPassword('tester@192.0.2.10', 'fixture-password');
      expect(requests, ['/v1/ssh/test', '/v1/ssh/target']);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('SSH preserves bounded service diagnostic', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    server.listen((request) async {
      await request.drain<void>();
      request.response.statusCode = 409;
      request.response.write(
          jsonEncode({'detail': 'Host key changed; review it independently.'}));
      await request.response.close();
    });
    try {
      await expectLater(
          api.testSshAlias('target'),
          throwsA(isA<SshConnectionFailure>()
              .having((e) => e.status, 'status', 409)
              .having((e) => e.message, 'detail',
                  'Host key changed; review it independently.')));
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('SSH refuses redirected credential requests', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    int requests = 0;
    server.listen((request) async {
      requests++;
      await request.drain<void>();
      request.response.statusCode = 302;
      request.response.headers
          .set('location', 'http://127.0.0.1:${server.port}/unexpected');
      await request.response.close();
    });
    try {
      await expectLater(api.testSshPassword('target', 'fixture-password'),
          throwsA(isA<SshConnectionFailure>()));
      expect(requests, 1);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('oversized diagnostic is discarded', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    server.listen((request) async {
      await request.drain<void>();
      request.response.statusCode = 409;
      request.response.write(jsonEncode({'detail': 'x' * 9000}));
      await request.response.close();
    });
    try {
      await expectLater(
          api.testSshAlias('target'),
          throwsA(isA<SshConnectionFailure>()
              .having((e) => e.message, 'detail', isEmpty)));
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
