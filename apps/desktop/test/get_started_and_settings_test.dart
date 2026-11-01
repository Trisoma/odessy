import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/setup/controllers/installed_manage_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/get_started_page.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';

import 'setup_usability_test.dart' show FakePicker;

class ConfigureBackend implements SetupBackend {
  final requests = <SetupRequest>[];
  bool configured = false;
  Json? initialSelection;
  String? failAction;
  bool failSpecAfterSave = false;

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (request.operation == 'discover') {
      return SetupReply.fromJson({
        'id': request.id,
        'status': 'ok',
        'result': {
          'discovery': {
            'status': 'ok',
            'endpoint': 'http://127.0.0.1:11434',
            'models': [
              {'name': 'llama3.1:8b', 'size_bytes': 1},
            ],
          }
        }
      }, request.id);
    }
    final action = request.parameters['action'] as String? ?? '';
    if (action == failAction ||
        (action == 'installed-spec' && failSpecAfterSave && configured)) {
      return SetupReply.fromJson(
          {'id': request.id, 'status': 'error', 'reason': 'Fixture failure'},
          request.id);
    }
    final result = switch (action) {
      'installed-spec' => {
          'root': 'C:/App',
          'installation_id': 'a' * 32,
          'role': 'combined',
          'model_configured': configured,
          'workspace': '',
          if (initialSelection != null) 'selection': initialSelection,
        },
      'package-status' => {
          'installation_format': 'versioned',
          'active': 'v1',
          'versions': ['v1'],
          'configuration': {
            'role': 'combined',
            'model_configured': configured,
            'workspace': '',
          },
        },
      'package-configure-review' => {'plan_id': 'configure-1'},
      'package-configure' => () {
          configured = true;
          initialSelection =
              Map<String, dynamic>.from(request.parameters['selection'] as Map);
          return {'configured': true, 'model_configured': true};
        }(),
      _ => {'ok': true},
    };
    return SetupReply.fromJson(
        {'id': request.id, 'status': 'ok', 'result': result}, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

void main() {
  for (final failing in [
    'package-configure-review',
    'package-configure',
    'reload'
  ]) {
    testWidgets('configuration $failing failure does not start chat',
        (tester) async {
      final backend = ConfigureBackend();
      final c = InstalledManageController(backend, spec: {'root': 'C:/App'});
      addTearDown(c.dispose);
      await c.load();
      if (failing == 'reload') {
        backend.failSpecAfterSave = true;
      } else {
        backend.failAction = failing;
      }
      var started = 0;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: GetStartedPage(controller: c, onStart: () => started++))));
      await tester.enterText(
          find.byKey(const Key('get-started-model')), 'local-model');
      await tester.pump();
      await tester.tap(find.text('Save and continue'));
      await tester.pumpAndSettle();
      expect(started, 0);
      expect(c.error, isNotEmpty);
    });
  }

  testWidgets('configured LFM backend and credential reference are preserved',
      (tester) async {
    final backend = ConfigureBackend()
      ..configured = true
      ..initialSelection = {
        'model': {
          'provider': 'lfm',
          'name': 'local-model',
          'endpoint': 'http://127.0.0.1:11435',
          'credential_env': 'LOCAL_API_KEY'
        }
      };
    final c = InstalledManageController(backend, spec: {'root': 'C:/App'});
    addTearDown(c.dispose);
    await c.load();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    await tester.tap(find.text('Change model'));
    await tester.pumpAndSettle();
    expect(find.text('LFM'), findsOneWidget);
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('get-started-model')))
            .controller!
            .text,
        'local-model');
    final localKey = find.widgetWithText(
        TextField, 'Local credential environment variable (optional)');
    expect(
        tester.widget<TextField>(localKey).controller!.text, 'LOCAL_API_KEY');
    await tester.tap(find.text('Save and continue'));
    await tester.pumpAndSettle();
    final review = backend.requests.firstWhere(
        (r) => r.parameters['action'] == 'package-configure-review');
    expect((review.parameters['selection'] as Json)['model'], {
      'provider': 'lfm',
      'name': 'local-model',
      'endpoint': 'http://127.0.0.1:11435',
      'credential_env': 'LOCAL_API_KEY'
    });
  });

  testWidgets('switching from local to cloud does not reuse the local endpoint',
      (tester) async {
    final c =
        InstalledManageController(ConfigureBackend(), spec: {'root': 'C:/App'});
    addTearDown(c.dispose);
    await c.load();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    await tester.tap(find.text('Remote or cloud').first);
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const Key('get-started-remote-endpoint')))
            .controller!
            .text,
        isEmpty);
  });

  testWidgets('deferring and returning retains the chosen cloud endpoint',
      (tester) async {
    final c =
        InstalledManageController(ConfigureBackend(), spec: {'root': 'C:/App'});
    addTearDown(c.dispose);
    await c.load();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    await tester.tap(find.text('Remote or cloud').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('get-started-remote-endpoint')),
        'https://provider.example');
    await tester.tap(find.widgetWithText(TextButton, 'Configure later').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure a model'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const Key('get-started-remote-endpoint')))
            .controller!
            .text,
        'https://provider.example');
  });

  testWidgets('configured remote model can be edited without losing its fields',
      (tester) async {
    final backend = ConfigureBackend()
      ..configured = true
      ..initialSelection = {
        'model': {
          'provider': 'claude',
          'name': 'example-model',
          'endpoint': 'https://provider.example',
          'credential_env': 'TEST_PROVIDER_KEY'
        }
      };
    final c = InstalledManageController(backend, spec: {'root': 'C:/App'});
    addTearDown(c.dispose);
    await c.load();
    var started = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(controller: c, onStart: () => started++))));
    await tester.tap(find.text('Change model'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const Key('get-started-remote-model')))
            .controller!
            .text,
        'example-model');
    expect(
        tester
            .widget<TextField>(
                find.byKey(const Key('get-started-remote-endpoint')))
            .controller!
            .text,
        'https://provider.example');
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('get-started-credential')))
            .controller!
            .text,
        'TEST_PROVIDER_KEY');
    expect(find.text('Claude'), findsOneWidget);
    expect(find.text('Save and continue'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('get-started-remote-model')), 'updated-model');
    await tester.tap(find.text('Save and continue'));
    await tester.pumpAndSettle();
    final request = backend.requests.firstWhere(
        (r) => r.parameters['action'] == 'package-configure-review');
    expect((request.parameters['selection'] as Json)['model'], {
      'provider': 'claude',
      'name': 'updated-model',
      'endpoint': 'https://provider.example',
      'credential_env': 'TEST_PROVIDER_KEY'
    });
    expect(started, 1);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    await tester.tap(find.text('Change model'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const Key('get-started-remote-model')))
            .controller!
            .text,
        'updated-model');
  });

  testWidgets(
      'voice row opens editable assets and endpoint row probes explicitly',
      (tester) async {
    final backend = ConfigureBackend();
    final c = InstalledManageController(backend, spec: {'root': 'C:/App'});
    await c.load();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    await tester.ensureVisible(find.byKey(const Key('checklist-endpoint')));
    await tester.tap(find.byKey(const Key('checklist-endpoint')));
    await tester.pumpAndSettle();
    expect(
        backend.requests.where((r) => r.operation == 'discover'), hasLength(1));
    await tester.ensureVisible(find.byKey(const Key('checklist-voice')));
    await tester.tap(find.byKey(const Key('checklist-voice')));
    await tester.pumpAndSettle();
    expect(find.text('Save voice configuration'), findsOneWidget);
    expect(find.text('whisper bin'), findsOneWidget);
    await tester.ensureVisible(find.text('Save voice configuration'));
    await tester.tap(find.text('Save voice configuration'));
    await tester.pumpAndSettle();
    final review = backend.requests.firstWhere(
        (r) => r.parameters['action'] == 'package-configure-review');
    expect((review.parameters['selection'] as Map)['voice'],
        containsPair('enabled', false));
  });
  Future<InstalledManageController> controller(ConfigureBackend backend) async {
    final c = InstalledManageController(backend, spec: {'root': 'C:/App'});
    await c.load();
    return c;
  }

  testWidgets('first run offers three explicit model choices', (tester) async {
    final backend = ConfigureBackend();
    final c = await controller(backend);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    expect(find.text('On this computer'), findsWidgets);
    expect(find.text('Remote or cloud'), findsWidgets);
    expect(find.text('Configure later'), findsWidgets);
    expect(find.text('Get started with Odessy'), findsOneWidget);
  });

  testWidgets('checklist opens model selection and workspace picker',
      (tester) async {
    final c = await controller(ConfigureBackend());
    final picker = FakePicker(directory: r'C:\chosen-workspace');
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(
                controller: c, picker: picker, onStart: () {}))));
    await tester.tap(find.text('Configure later').first);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('checklist-model')));
    await tester.tap(find.byKey(const Key('checklist-model')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('get-started-model')), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('checklist-workspace')));
    await tester.tap(find.byKey(const Key('checklist-workspace')));
    await tester.pumpAndSettle();
    expect(picker.directoryCalls, 1);
    expect(c.modelConfigured, isFalse);
  });

  testWidgets('discovery is explicit and lists installed models',
      (tester) async {
    final backend = ConfigureBackend();
    final c = await controller(backend);
    var started = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(controller: c, onStart: () => started++))));
    expect(find.byKey(const Key('get-started-model-list')), findsNothing);
    await tester.tap(find.byKey(const Key('get-started-discover')));
    await tester.pumpAndSettle();
    expect(backend.requests.where((r) => r.operation == 'discover').length, 1);
    expect(find.byKey(const Key('get-started-model-list')), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('get-started-model-list')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('get-started-model-list')));
    await tester.pumpAndSettle();
    expect(find.text('llama3.1:8b'), findsWidgets);
    expect(started, 0, reason: 'discovery must never start the coordinator');
  });

  testWidgets('saving reviews the exact selection and workspace',
      (tester) async {
    final backend = ConfigureBackend();
    final c = await controller(backend);
    var started = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(
                controller: c,
                picker: FakePicker(directory: r'C:\work'),
                onStart: () => started++))));
    await tester.enterText(
        find.byKey(const Key('get-started-model')), 'llama3.1:8b');
    await tester.enterText(
        find.byKey(const Key('get-started-workspace')), r'C:\work');
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save and continue'));
    await tester.pumpAndSettle();
    final review = backend.requests.firstWhere(
        (r) => r.parameters['action'] == 'package-configure-review');
    expect((review.parameters['selection'] as Json)['model'], {
      'provider': 'ollama',
      'name': 'llama3.1:8b',
      'endpoint': 'http://127.0.0.1:11434',
    });
    expect(review.parameters['workspace'], r'C:\work');
    final applied = backend.requests
        .firstWhere((r) => r.parameters['action'] == 'package-configure');
    expect(applied.parameters['confirm'], 'configure-1');
    expect(started, 1);
  });

  testWidgets('footer configure later keeps the window open without mutations',
      (tester) async {
    final backend = ConfigureBackend();
    final c = await controller(backend);
    var closed = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(
                controller: c, onStart: () {}, onClose: () => closed++))));
    await tester.tap(find.widgetWithText(TextButton, 'Configure later').last);
    await tester.pumpAndSettle();
    expect(closed, 0);
    expect(find.textContaining('Model setup is deferred'), findsOneWidget);
    await tester.tap(find.text('Configure a model'));
    await tester.pumpAndSettle();
    expect(closed, 0);
    expect(find.byKey(const Key('get-started-model')), findsOneWidget);
    expect(
        backend.requests
            .where((r) => r.parameters['action'] == 'package-configure-review'),
        isEmpty);
  });

  testWidgets('settings expose every section and keep the installation entry',
      (tester) async {
    tester.view.physicalSize = const Size(1100, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var openedManagement = 0, openedConfiguration = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SettingsPage(
                controller: DesktopSession(DemoApi()),
                installed: true,
                modelConfigured: false,
                onOpenManagement: () => openedManagement++,
                onOpenGetStarted: () => openedConfiguration++))));
    await tester.pumpAndSettle();
    for (final name in SettingsSections.all) {
      expect(find.text(name), findsWidgets, reason: 'missing section $name');
    }
    await tester.tap(find.byKey(
        const ValueKey('settings-section-${SettingsSections.installation}')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Manage installation'));
    expect(openedManagement, 1);
    await tester.tap(find
        .byKey(const ValueKey('settings-section-${SettingsSections.models}')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Configure a model'));
    await tester.tap(find.text('Configure a model'));
    expect(openedConfiguration, 1);
    expect(find.byKey(const Key('consultation-limit')), findsWidgets);
  });
}
