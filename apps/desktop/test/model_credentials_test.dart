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

void main() {
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
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf'))).load();
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
}
