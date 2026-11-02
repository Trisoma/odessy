import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('collaboration state requires authenticated boolean responses',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var invalid = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/settings/collaboration-enabled');
      if (request.method == 'PUT') {
        final data = jsonDecode(await utf8.decoder.bind(request).join());
        expect(data['enabled'], true);
      }
      request.response.write(jsonEncode({
        'instance_id': 'instance',
        'revision': 3,
        'enabled': invalid ? 1 : true
      }));
      await request.response.close();
    });
    try {
      final current = await api.readCollaborationEnabled();
      await api.saveCollaborationEnabled(current);
      invalid = true;
      await expectLater(api.readCollaborationEnabled(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('round limit uses authenticated requests and rejects invalid values',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var invalid = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/settings/round-limit');
      if (request.method == 'PUT') {
        final data = jsonDecode(await utf8.decoder.bind(request).join());
        expect(data['max_rounds'], 1);
      }
      request.response.write(jsonEncode({
        'instance_id': 'instance',
        'revision': 3,
        'max_rounds': invalid ? 33 : 1
      }));
      await request.response.close();
    });
    try {
      final current = await api.readRoundLimit();
      await api.saveRoundLimit(current);
      invalid = true;
      await expectLater(api.readRoundLimit(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test(
      'context limit uses authenticated versioned requests and validates results',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var invalid = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/settings/context-limit');
      if (request.method == 'PUT') {
        final data = jsonDecode(await utf8.decoder.bind(request).join());
        expect(data['revision'], 3);
      }
      request.response.write(jsonEncode({
        'instance_id': 'instance',
        'revision': 3,
        'max_context_chars': invalid ? 0 : 8000
      }));
      await request.response.close();
    });
    try {
      final current = await api.readContextLimit();
      await api.saveContextLimit(current);
      invalid = true;
      await expectLater(api.readContextLimit(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('limit operations use authentication, version and strict responses',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/settings/consultation-limit');
      if (request.method == 'PUT') {
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body,
            {'instance_id': 'instance', 'revision': 3, 'max_consultations': 4});
      }
      request.response.write(jsonEncode({
        'instance_id': 'instance',
        'revision': 3,
        'max_consultations': malformed ? true : 4
      }));
      await request.response.close();
    });
    try {
      final version = await api.readConsultationLimit();
      await api.saveConsultationLimit(version);
      malformed = true;
      await expectLater(api.readConsultationLimit(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
