import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  test('model limits use authenticated routes and reject invalid responses',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var invalid = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/settings/model-profiles/assistant/limits');
      if (request.method == 'GET') {
        request.response.write(jsonEncode({
          'instance_id': 'instance',
          'revision': 1,
          'timeout': invalid ? 0 : 120,
          'max_tokens': 4096
        }));
      } else {
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body['timeout'], 45);
        request.response.write(jsonEncode(
            {'instance_id': 'instance', 'revision': 2, 'profiles': []}));
      }
      await request.response.close();
    });
    try {
      expect((await api.modelProfileLimits('assistant'))['timeout'], 120);
      await api.saveModelProfileLimits('assistant', {
        'instance_id': 'instance',
        'revision': 1,
        'timeout': 45,
        'max_tokens': 2000
      });
      invalid = true;
      await expectLater(
          api.modelProfileLimits('assistant'), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('credential reference response contains metadata only', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path,
          '/v1/settings/model-profiles/assistant/credential-reference');
      if (request.method == 'GET') {
        request.response.write(jsonEncode({
          'instance_id': 'instance',
          'revision': 3,
          'credential_env': '',
          'default_credential_env': 'OPENAI_API_KEY',
          if (malformed) 'value': 'must-not-be-accepted'
        }));
      } else {
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body['credential_env'], 'CUSTOM_API_KEY');
        request.response.write(jsonEncode(
            {'instance_id': 'instance', 'revision': 4, 'profiles': []}));
      }
      await request.response.close();
    });
    try {
      expect(
          (await api
              .modelCredentialReference('assistant'))['default_credential_env'],
          'OPENAI_API_KEY');
      await api.saveModelCredentialReference('assistant', {
        'instance_id': 'instance',
        'revision': 3,
        'credential_env': 'CUSTOM_API_KEY'
      });
      malformed = true;
      await expectLater(
          api.modelCredentialReference('assistant'), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('pricing is authenticated and malformed values are rejected', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      expect(request.uri.path, '/v1/settings/model-profiles/assistant/pricing');
      if (request.method == 'GET') {
        request.response.write(jsonEncode({
          'instance_id': 'instance',
          'revision': 3,
          'pricing': malformed
              ? {'input': -1, 'cached_input': 0, 'output': 1, 'currency': 'USD'}
              : null
        }));
      } else {
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body['pricing'], isNull);
        request.response.write(jsonEncode(
            {'instance_id': 'instance', 'revision': 4, 'profiles': []}));
      }
      await request.response.close();
    });
    try {
      expect((await api.modelProfilePricing('assistant'))['pricing'], isNull);
      await api.saveModelProfilePricing('assistant',
          {'instance_id': 'instance', 'revision': 3, 'pricing': null});
      malformed = true;
      await expectLater(
          api.modelProfilePricing('assistant'), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('profile creation and deletion use the authenticated management routes',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final requests = <String>[];
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      requests.add('${request.method} ${request.uri.path}');
      if (request.method != 'GET') {
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body['revision'], 7);
      }
      request.response.write(jsonEncode(request.method == 'GET'
          ? {
              'providers': [
                {'name': 'openai', 'credential_env': 'OPENAI_API_KEY'}
              ]
            }
          : {'instance_id': 'fixture', 'revision': 8, 'profiles': []}));
      await request.response.close();
    });
    try {
      expect((await api.modelProfileProviders()).single['name'], 'openai');
      await api.createModelProfile({'instance_id': 'fixture', 'revision': 7});
      await api.deleteModelProfile(
          'assistant', {'instance_id': 'fixture', 'revision': 7});
      await api.switchModelProvider('assistant', {
        'instance_id': 'fixture',
        'revision': 7,
        'provider': 'claude',
        'model': '',
        'endpoint': ''
      });
      expect(requests, [
        'GET /v1/settings/model-profile-providers',
        'POST /v1/settings/model-profiles',
        'DELETE /v1/settings/model-profiles/assistant',
        'PUT /v1/settings/model-profiles/assistant/provider'
      ]);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
  test('model profile requests are scoped, authenticated and validated',
      () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final api =
        LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    var malformed = false;
    server.listen((request) async {
      expect(request.headers.value('authorization'), 'Bearer fixture');
      if (request.method == 'PUT') {
        expect(request.uri.path, '/v1/settings/model-profiles/assistant');
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body['revision'], 3);
        expect(body['model'], 'model-b');
      }
      request.response.write(jsonEncode({
        'instance_id': 'fixture',
        'revision': 3,
        'profiles': [
          {
            'name': 'assistant',
            'adapter': 'cloud',
            'provider': 'openai',
            'model': 'model-b',
            'endpoint': '',
            if (malformed) 'secret': 'unexpected'
          }
        ]
      }));
      await request.response.close();
    });
    try {
      await api.modelProfiles();
      await api.updateModelProfile('assistant', {
        'instance_id': 'fixture',
        'revision': 3,
        'model': 'model-b',
        'endpoint': ''
      });
      malformed = true;
      await expectLater(api.modelProfiles(), throwsFormatException);
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
