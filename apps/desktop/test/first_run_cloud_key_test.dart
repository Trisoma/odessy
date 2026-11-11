import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/installed_manage_controller.dart';
import 'package:odessy_desktop/features/setup/presentation/get_started_page.dart';
import 'package:odessy_desktop/features/settings/presentation/model_credential_panel.dart';

/// A first-run installation whose provider key is managed by the existing
/// `model-credential` operation rather than by environment variables.
class FirstRunBackend implements SetupBackend {
  FirstRunBackend({this.secureStorage = true});

  final bool secureStorage;
  final requests = <SetupRequest>[];
  String source = 'none';
  Json? configuredSelection;

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (request.operation == 'model-credential') {
      final action = request.parameters['action'];
      if (action == 'set') {
        source = request.parameters['storage'] as String? ?? 'session';
      }
      if (action == 'remove') source = 'none';
      return _ok(request, {
        'scope': 'scope-fixture',
        'configured': source != 'none',
        'source': source,
        'writable': true,
        'persistence': source == 'keyring' ? 'persistent' : 'session',
        'secure_storage_available': secureStorage,
        'message': '',
      });
    }
    if (request.operation == 'model-catalog') {
      return _ok(request, {
        'status': 'suggestions',
        'models': [
          {
            'name': 'deepseek-flash',
            'label': 'Provider default (deepseek-flash)'
          }
        ],
      });
    }
    if (request.operation == 'discover') {
      return _ok(request, {
        'discovery': {'status': 'unreachable', 'models': []}
      });
    }
    final action = request.parameters['action'] as String? ?? '';
    return _ok(
        request,
        switch (action) {
          'installed-spec' => {
              'root': 'C:/App',
              'installation_id': 'b' * 32,
              'role': 'combined',
              'model_configured': false,
              'workspace': '',
            },
          'package-status' => {
              'installation_format': 'versioned',
              'active': 'v1',
              'versions': ['v1'],
              'configuration': {
                'role': 'combined',
                'model_configured': false,
                'workspace': '',
              },
            },
          'package-configure-review' => {'plan_id': 'configure-1'},
          'package-configure' => () {
              configuredSelection = Map<String, dynamic>.from(
                  request.parameters['selection'] as Map);
              return {'configured': true, 'model_configured': true};
            }(),
          _ => {'ok': true},
        });
  }

  SetupReply _ok(SetupRequest request, Json result) => SetupReply.fromJson(
      {'id': request.id, 'status': 'ok', 'result': result}, request.id);

  List<SetupRequest> operations(String operation) =>
      requests.where((r) => r.operation == operation).toList();

  /// Only the writes, so a status read does not make the lookup ambiguous.
  List<SetupRequest> get setRequests => requests
      .where((r) =>
          r.operation == 'model-credential' && r.parameters['action'] == 'set')
      .toList();

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

Future<InstalledManageController> _controller(FirstRunBackend backend) async {
  final controller =
      InstalledManageController(backend, spec: {'root': 'C:/App'});
  await controller.load();
  return controller;
}

Future<void> _openRemote(WidgetTester tester) async {
  await tester.tap(find.text('Remote or cloud'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'a fresh install can save a cloud key without environment variables',
      (tester) async {
    final backend = FirstRunBackend();
    final c = await _controller(backend);
    addTearDown(c.dispose);
    var started = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(controller: c, onStart: () => started++))));
    await tester.pumpAndSettle();
    await _openRemote(tester);

    expect(find.byType(ModelCredentialPanel), findsOneWidget);
    expect(find.text('No API key configured'), findsOneWidget);

    await tester.enterText(find.byKey(const ValueKey('model-api-key')),
        'sk-fixture-not-a-real-key');
    await tester.ensureVisible(find.text('Save securely'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save securely'));
    await tester.pumpAndSettle();

    final save = backend.setRequests.single;
    expect(save.parameters['action'], 'set');
    expect(save.parameters['storage'], 'keyring');
    expect(save.parameters['provider'], 'openai');
    expect(save.parameters['value'], 'sk-fixture-not-a-real-key');
    expect(find.text('Configured · OS credential store'), findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('get-started-remote-model')), 'deepseek-chat');
    await tester.pump();
    await tester.ensureVisible(find.text('Save and continue'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save and continue'));
    await tester.pumpAndSettle();

    expect(started, 1);
    final selection = backend.configuredSelection!;
    final serialized = selection.toString();
    expect(serialized, isNot(contains('sk-fixture-not-a-real-key')));
    expect((selection['model'] as Map)['provider'], 'openai');
    expect((selection['model'] as Map)['name'], 'deepseek-chat');
  });

  testWidgets('no secure storage still allows a session-only key',
      (tester) async {
    final backend = FirstRunBackend(secureStorage: false);
    final c = await _controller(backend);
    addTearDown(c.dispose);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: GetStartedPage(controller: c, onStart: () {}))));
    await tester.pumpAndSettle();
    await _openRemote(tester);

    await tester.enterText(
        find.byKey(const ValueKey('model-api-key')), 'sk-session-fixture');
    await tester.pump();
    // The persistent save is unavailable; the explicit session path is not.
    final secure = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Save securely'));
    expect(secure.onPressed, isNull);
    await tester.ensureVisible(find.text('Use for this session only'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Use for this session only'));
    await tester.pumpAndSettle();

    final save = backend.setRequests.single;
    expect(save.parameters['storage'], 'session');
    expect(find.text('Configured · this session only'), findsOneWidget);
  });

  testWidgets('configure later neither contacts a provider nor stores a key',
      (tester) async {
    final backend = FirstRunBackend();
    final c = await _controller(backend);
    addTearDown(c.dispose);
    var started = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: GetStartedPage(controller: c, onStart: () => started++))));
    await tester.pumpAndSettle();
    await tester.tap(find.descendant(
        of: find.byType(SegmentedButton<String>),
        matching: find.text('Configure later')));
    await tester.pumpAndSettle();

    expect(find.textContaining('Model setup is deferred'), findsOneWidget);
    expect(backend.operations('model-credential'), isEmpty);
    expect(started, 0);
  });

  testWidgets('a changed endpoint is not re-read on every keystroke',
      (tester) async {
    final backend = FirstRunBackend();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ModelCredentialPanel(
                backend: backend,
                provider: 'deepseek',
                endpoint: '',
                reference: ''))));
    await tester.pumpAndSettle();
    expect(backend.operations('model-credential'), hasLength(1));

    for (final text in ['h', 'ht', 'htt', 'http://127.0.0.1:9']) {
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: ModelCredentialPanel(
                  backend: backend,
                  provider: 'deepseek',
                  endpoint: text,
                  reference: ''))));
      await tester.pump();
    }
    expect(backend.operations('model-credential'), hasLength(1));
    expect(find.textContaining('changed since this status was read'),
        findsOneWidget);

    await tester.tap(find.text('Refresh key status'));
    await tester.pumpAndSettle();
    final reads = backend.operations('model-credential');
    expect(reads, hasLength(2));
    expect(reads.last.parameters['endpoint'], 'http://127.0.0.1:9');
  });
}
