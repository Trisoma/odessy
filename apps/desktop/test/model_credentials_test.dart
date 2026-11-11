import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/settings/presentation/model_credential_panel.dart';

class CredentialBackend implements SetupBackend {
  String source = 'none';
  final requests = <SetupRequest>[];
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (request.parameters['action'] == 'set') {
      source = request.parameters['storage'] as String;
    }
    if (request.parameters['action'] == 'remove') source = 'none';
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'scope': 'scope-test',
        'configured': source != 'none',
        'source': source,
        'writable': true,
        'persistence': source == 'keyring'
            ? 'persistent'
            : source == 'session'
                ? 'session'
                : 'none',
        'secure_storage_available': true,
        'message': '',
      }
    }, request.id);
  }

  @override
  Future<void> close() async {}
  @override
  Future<void> cancel() async {}
}

/// Reports one credential per (provider, endpoint, reference) scope, so a test
/// can prove which scope a destructive action reaches.
class ScopedCredentialBackend implements SetupBackend {
  final stored = <String, String>{};
  final removals = <String>[];
  final reads = <String>[];

  @override
  Future<SetupReply> execute(SetupRequest request) async {
    final endpoint = request.parameters['endpoint'] as String? ?? '';
    final action = request.parameters['action'] as String?;
    if (action == 'describe') reads.add(endpoint);
    if (action == 'set') {
      stored[endpoint] = request.parameters['storage'] as String? ?? 'session';
    }
    if (action == 'remove') {
      removals.add(endpoint);
      stored.remove(endpoint);
    }
    final source = stored[endpoint] ?? 'none';
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'scope': 'scope-$endpoint',
        'configured': source != 'none',
        'source': source,
        'writable': true,
        'persistence': source == 'keyring'
            ? 'persistent'
            : source == 'session'
                ? 'session'
                : 'none',
        'secure_storage_available': true,
        'message': '',
      }
    }, request.id);
  }

  @override
  Future<void> cancel() async {}
  @override
  Future<void> close() async {}
}

Widget _panel(SetupBackend backend, String endpoint) => MaterialApp(
    home: Scaffold(
        body: SingleChildScrollView(
            child: ModelCredentialPanel(
                backend: backend,
                provider: 'deepseek',
                endpoint: endpoint,
                reference: ''))));

void main() {
  testWidgets('environment credential permits explicit replacement',
      (tester) async {
    final backend = CredentialBackend()..source = 'env';
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
      child: ModelCredentialPanel(
          backend: backend, provider: 'deepseek', endpoint: '', reference: ''),
    ))));
    await tester.pumpAndSettle();
    final field = find.byKey(const ValueKey('model-api-key'));
    expect(tester.widget<TextField>(field).enabled, isTrue);
    expect(find.textContaining('environment'), findsWidgets);
    expect(find.textContaining('read-only'), findsNothing);
    await tester.enterText(field, 'replacement-test-key');
    await tester.tap(find.text('Save securely'));
    await tester.pumpAndSettle();
    expect(backend.requests.last.parameters['storage'], 'keyring');
    expect(find.textContaining('OS credential store'), findsOneWidget);
    expect(find.text('replacement-test-key'), findsNothing);
  });
  testWidgets(
      'credential controls wrap at large text and render without exposing values',
      (tester) async {
    tester.view.physicalSize = const Size(1100, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final boundaryKey = GlobalKey();
    final backend = CredentialBackend()..source = 'keyring';
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: RepaintBoundary(
                key: boundaryKey,
                child: MediaQuery(
                    data: const MediaQueryData(
                        textScaler: TextScaler.linear(1.5)),
                    child: SingleChildScrollView(
                        child: Padding(
                            padding: const EdgeInsets.all(24),
                            child: ModelCredentialPanel(
                                backend: backend,
                                provider: 'deepseek',
                                endpoint: '',
                                reference: ''))))))));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('Save securely'), findsOneWidget);
    await tester.runAsync(() async {
      final boundary = boundaryKey.currentContext!.findRenderObject()!
          as RenderRepaintBoundary;
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final file = File('build/preview/secure-credentials.png');
      await file.parent.create(recursive: true);
      await file.writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'key entry is masked, never added to component settings, and cleared after save',
      (tester) async {
    final backend = CredentialBackend();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: ModelCredentialPanel(
                    backend: backend,
                    provider: 'deepseek',
                    endpoint: '',
                    reference: '')))));
    await tester.pumpAndSettle();
    final finder = find.byKey(const ValueKey('model-api-key'));
    expect(tester.widget<TextField>(finder).obscureText, isTrue);
    await tester.enterText(finder, 'test-sentinel-key');
    await tester.tap(find.text('Save securely'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(finder).controller!.text, isEmpty);
    expect(find.textContaining('Saved securely'), findsOneWidget);
    expect(backend.requests.every((r) => r.operation == 'model-credential'),
        isTrue);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ModelCredentialPanel(
                backend: backend,
                provider: 'deepseek',
                endpoint: '',
                reference: ''))));
    await tester.pumpAndSettle();
    expect(find.textContaining('OS credential store'), findsOneWidget);
    expect(find.text('test-sentinel-key'), findsNothing);
  });

  testWidgets('a changed scope cannot remove the previous endpoint key',
      (tester) async {
    final backend = ScopedCredentialBackend()..stored[''] = 'keyring';
    await tester.pumpWidget(_panel(backend, ''));
    await tester.pumpAndSettle();
    expect(find.text('Configured \u00b7 OS credential store'), findsOneWidget);
    expect(
        tester
            .widget<TextButton>(find.widgetWithText(TextButton, 'Remove'))
            .onPressed,
        isNotNull);

    // Same panel, new endpoint: the shown status now describes another scope.
    await tester.pumpWidget(_panel(backend, 'https://proxy.example/v1'));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('Removing a key stays disabled'), findsOneWidget);
    expect(
        tester
            .widget<TextButton>(find.widgetWithText(TextButton, 'Remove'))
            .onPressed,
        isNull);
    expect(backend.removals, isEmpty);
    expect(backend.stored[''], 'keyring');

    // Re-reading the new scope makes the new scope's own key removable.
    await tester.ensureVisible(find.text('Refresh key status'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Refresh key status'));
    await tester.pumpAndSettle();
    expect(backend.reads.last, 'https://proxy.example/v1');
    await tester.enterText(
        find.byKey(const ValueKey('model-api-key')), 'sk-new-scope');
    await tester.ensureVisible(find.text('Save securely'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save securely'));
    await tester.pumpAndSettle();
    expect(backend.stored['https://proxy.example/v1'], 'keyring');
    await tester.ensureVisible(find.widgetWithText(TextButton, 'Remove'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, 'Remove'));
    await tester.pumpAndSettle();
    expect(backend.removals, ['https://proxy.example/v1']);
    expect(backend.stored[''], 'keyring',
        reason: 'the previous endpoint key must survive');
  });
}
