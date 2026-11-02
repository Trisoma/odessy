import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/model_profiles.dart';
import 'package:odessy_desktop/features/settings/controllers/model_profiles_controller.dart';

class ProfileFixture extends DemoApi
    implements
        ModelProfilesApi,
        ModelProfileManagementApi,
        ModelProviderSwitchApi,
        ModelProfilePricingApi,
        ModelProfileLimitsApi,
        ModelCredentialReferenceApi {
  @override
  bool get isDemo => false;
  int revision = 0, writes = 0;
  String model = 'old', endpoint = 'https://example.test';
  String provider = 'openai';
  Json? price;
  String credentialEnv = '';
  bool hasExtra = false, hasAssistant = true;
  int timeout = 120, maxTokens = 4096;
  @override
  Future<Json> modelProfileLimits(String name) async => {
        'instance_id': 'fixture',
        'revision': revision,
        'timeout': timeout,
        'max_tokens': maxTokens
      };
  @override
  Future<Json> saveModelProfileLimits(String name, Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    timeout = update['timeout'] as int;
    maxTokens = update['max_tokens'] as int;
    revision++;
    writes++;
    return modelProfiles();
  }

  @override
  Future<Json> modelProfiles() async => {
        'instance_id': 'fixture',
        'revision': revision,
        'profiles': [
          if (hasAssistant)
            {
              'name': 'assistant',
              'adapter': 'cloud',
              'provider': provider,
              'model': model,
              'endpoint': endpoint
            },
          if (hasExtra)
            {
              'name': 'new',
              'adapter': 'ollama',
              'provider': '',
              'model': '',
              'endpoint': ''
            },
        ]
      };
  @override
  Future<Json> updateModelProfile(String name, Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    writes++;
    model = update['model'] as String;
    endpoint = update['endpoint'] as String;
    revision++;
    return modelProfiles();
  }

  @override
  Future<List<Json>> modelProfileProviders() async => [
        {'name': 'openai', 'credential_env': 'OPENAI_API_KEY'},
        {'name': 'claude', 'credential_env': 'ANTHROPIC_API_KEY'}
      ];
  @override
  Future<Json> switchModelProvider(String name, Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    provider = update['provider'] as String;
    model = update['model'] as String;
    endpoint = update['endpoint'] as String;
    revision++;
    writes++;
    return modelProfiles();
  }

  @override
  Future<Json> modelProfilePricing(String name) async =>
      {'instance_id': 'fixture', 'revision': revision, 'pricing': price};
  @override
  Future<Json> saveModelProfilePricing(String name, Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    price = update['pricing'] as Json?;
    revision++;
    writes++;
    return modelProfiles();
  }

  @override
  Future<Json> modelCredentialReference(String name) async => {
        'instance_id': 'fixture',
        'revision': revision,
        'credential_env': credentialEnv,
        'default_credential_env': 'OPENAI_API_KEY'
      };
  @override
  Future<Json> saveModelCredentialReference(String name, Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    credentialEnv = update['credential_env'] as String;
    revision++;
    writes++;
    return modelProfiles();
  }

  @override
  Future<Json> createModelProfile(Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    expect(update['name'], 'new');
    hasExtra = true;
    revision++;
    writes++;
    return modelProfiles();
  }

  @override
  Future<Json> deleteModelProfile(String name, Json update) async {
    if (update['revision'] != revision) throw const ApiFailure(409);
    expect(name, 'assistant');
    hasAssistant = false;
    revision++;
    writes++;
    return modelProfiles();
  }
}

void main() {
  testWidgets('edits validated model request limits', (tester) async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Request limits…'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Timeout (seconds)'), '0');
    await tester.tap(find.text('Save limits'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    expect(find.textContaining('Enter a timeout'), findsOneWidget);
    await tester.enterText(
        find.widgetWithText(TextField, 'Timeout (seconds)'), '45');
    await tester.enterText(
        find.widgetWithText(TextField, 'Maximum output tokens'), '2000');
    await tester.tap(find.text('Save limits'));
    await tester.pumpAndSettle();
    expect(api.timeout, 45);
    expect(api.maxTokens, 2000);
  });
  test('a stale dialog cannot use a later configuration snapshot', () async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    final controller = ModelProfilesController(session);
    await Future<void>.delayed(Duration.zero);
    final old = controller.document!;
    await controller.refresh();
    expect(await controller.save('assistant', 'draft', '', old), isFalse);
    expect(await controller.remove('assistant', old), isFalse);
    expect(await controller.switchProvider('assistant', 'claude', '', '', old),
        isFalse);
    expect(await controller.savePricing('assistant', null, old), isFalse);
    expect(
        await controller.saveCredentialReference(
            'assistant', 'CUSTOM_API_KEY', old),
        isFalse);
    expect(api.writes, 0);
    controller.dispose();
    session.dispose();
  });
  testWidgets(
      'credential reference accepts names and never requests secret values',
      (tester) async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Credential reference…'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Do not paste'), findsOneWidget);
    expect(find.text('Provider default: OPENAI_API_KEY'), findsOneWidget);
    final field =
        find.widgetWithText(TextField, 'Credential environment variable');
    await tester.enterText(field, 'ODESSY_API_TOKEN');
    await tester.tap(find.text('Save reference'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    await tester.enterText(field, 'CUSTOM_API_KEY');
    await tester.tap(find.text('Save reference'));
    await tester.pumpAndSettle();
    expect(api.credentialEnv, 'CUSTOM_API_KEY');
  });
  testWidgets('pricing is unknown until entered and requires explicit values',
      (tester) async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pricing…'));
    await tester.pumpAndSettle();
    expect(find.text('No profile prices are configured.'), findsOneWidget);
    await tester.tap(find.text('Save prices'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    await tester.enterText(find.widgetWithText(TextField, 'Input price'), '2');
    await tester.enterText(
        find.widgetWithText(TextField, 'Cached input price'), '0.5');
    await tester.enterText(find.widgetWithText(TextField, 'Output price'), '4');
    await tester.enterText(find.widgetWithText(TextField, 'Currency'), 'USD');
    await tester.tap(find.text('Save prices'));
    await tester.pumpAndSettle();
    expect(api.price,
        {'input': 2.0, 'cached_input': 0.5, 'output': 4.0, 'currency': 'USD'});
  });
  testWidgets('clearing configured pricing requires confirmation',
      (tester) async {
    final api = ProfileFixture()
      ..price = {
        'input': 2.0,
        'cached_input': 0.5,
        'output': 4.0,
        'currency': 'USD'
      };
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pricing…'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Clear…'));
    await tester.pumpAndSettle();
    expect(api.price, isNotNull);
    await tester.tap(find.text('Clear pricing'));
    await tester.pumpAndSettle();
    expect(api.price, isNull);
  });
  testWidgets(
      'provider change uses fresh fields and displays credential source',
      (tester) async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Change provider…'));
    await tester.pumpAndSettle();
    expect(find.textContaining('clears its price settings'), findsOneWidget);
    expect(
        tester
            .widget<TextField>(
                find.widgetWithText(TextField, 'New model name (optional)'))
            .controller!
            .text,
        '');
    expect(
        tester
            .widget<TextField>(find.widgetWithText(
                TextField, 'New service endpoint (optional)'))
            .controller!
            .text,
        '');
    await tester.tap(find.text('New provider'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('claude').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('ANTHROPIC_API_KEY'), findsOneWidget);
    await tester.tap(find.text('Change provider'));
    await tester.pumpAndSettle();
    expect(api.provider, 'claude');
    expect(api.model, '');
    expect(api.endpoint, '');
  });
  testWidgets('updates selected profile and preserves failed draft on conflict',
      (tester) async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Model name'), 'new-model');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(api.model, 'new-model');
    expect(api.writes, 1);
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Model name'), 'draft');
    api.revision++;
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Save was not confirmed'), findsOneWidget);
    expect(find.text('draft'), findsOneWidget);
    expect(api.writes, 1);
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, 'Save'))
            .onPressed,
        isNull);
  });
  testWidgets('creates a profile and requires confirmation before deletion',
      (tester) async {
    final api = ProfileFixture();
    final session = DesktopSession(api);
    await session.start();
    addTearDown(session.dispose);
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: ModelProfiles(session: session))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Add model profile'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Profile name'), 'new');
    await tester.tap(find.text('Create profile'));
    await tester.pumpAndSettle();
    expect(api.hasExtra, isTrue);
    await tester.tap(find.text('assistant'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete…'));
    await tester.pumpAndSettle();
    expect(api.hasAssistant, isTrue);
    await tester.tap(find.text('Delete profile'));
    await tester.pumpAndSettle();
    expect(api.hasAssistant, isFalse);
  });
}
