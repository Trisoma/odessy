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
    final result = switch (action) {
      'installed-spec' => {
          'root': 'C:/App',
          'installation_id': 'a' * 32,
          'role': 'combined',
          'model_configured': configured,
          'workspace': '',
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
    await tester.tap(find.text(SettingsSections.installation).last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Manage installation'));
    expect(openedManagement, 1);
    await tester.tap(find.text(SettingsSections.models).last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Configure a model'));
    expect(openedConfiguration, 1);
    expect(find.byKey(const Key('consultation-limit')), findsWidgets);
  });
}
