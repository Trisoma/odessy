import 'dart:async';
import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/settings/controllers/component_settings_controller.dart';
import 'component_settings_test.dart' show SavedComponents;

class UncertainBackend extends SavedComponents {
  int saves = 0;
  bool loseReply = true;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.parameters['action'] == 'save') {
      saves++;
      final result = await super.execute(request);
      if (loseReply) throw StateError('Helper disconnected');
      return result;
    }
    return super.execute(request);
  }
}

class PendingBackend extends SavedComponents implements SetupMetadataBackend {
  final gate = Completer<void>();
  int closes = 0;
  bool started = false;
  @override
  SetupBackend createMetadataReader() => PendingReader(this);
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.parameters['action'] == 'save') {
      started = true;
      await gate.future;
    }
    return super.execute(request);
  }

  @override
  Future<void> close() async {
    closes++;
  }
}

class PendingReader implements SetupBackend {
  PendingReader(this.source);
  final PendingBackend source;
  @override
  Future<SetupReply> execute(SetupRequest request) => source.execute(request);
  @override
  Future<void> close() => source.close();
  @override
  Future<void> cancel() => source.cancel();
}

void main() {
  testWidgets('normal closure can be cancelled before owned shutdown',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var shutdowns = 0;
    await tester.pumpWidget(OdessyApp(
        api: DemoApi(),
        setupBackend: SavedComponents(),
        onShutdownOwned: () async {
          shutdowns++;
        }));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('consultation-limit')), '3');
    final exit = tester.binding.handleRequestAppExit();
    await tester.pumpAndSettle();
    expect(shutdowns, 0);
    await tester.tap(find.text('Keep editing'));
    await tester.pumpAndSettle();
    expect(await exit, AppExitResponse.cancel);
    expect(shutdowns, 0);
    await tester.pumpWidget(const SizedBox());
  });
  test(
      'reviewed merge preserves fresh untouched fields and requires a new save',
      () async {
    final backend = SavedComponents();
    final c = ComponentSettingsController(backend);
    await c.load();
    c.draft('tts', {
      'tts_provider': 'kokoro',
      'kokoro_model': 'old',
      'kokoro_voices': 'first'
    });
    c.edit('tts', {
      'tts_provider': 'kokoro',
      'kokoro_model': 'my-edit',
      'kokoro_voices': 'first'
    });
    (backend.selection['voice'] as Json)['kokoro_voices'] = 'other-writer';
    backend.revision = 2;
    final review = await c.reviewMerge('tts');
    expect(review, isNotNull);
    expect(backend.requests.where((r) => r.parameters['action'] == 'save'),
        isEmpty);
    expect(c.acceptMerge('tts', review!), isTrue);
    expect(c.drafts['tts']!.values['kokoro_model'], 'my-edit');
    expect(c.drafts['tts']!.values['kokoro_voices'], 'other-writer');
    expect(c.drafts['tts']!.revision, '2');
    expect(c.drafts['tts']!.dirty, isTrue);
    c.dispose();
  });
  test('uncertain write cannot be resubmitted without inspection', () async {
    final backend = UncertainBackend();
    final c = ComponentSettingsController(backend);
    await c.load();
    await c.save('tts', {'tts_provider': 'custom_audio:factory'});
    await c.save('tts', {'tts_provider': 'custom_audio:factory'});
    expect(backend.saves, 1);
    expect(c.status, contains('Inspect'));
    c.dispose();
  });

  test(
      'disposing an editor owner drains an admitted write before closing helper',
      () async {
    final backend = PendingBackend();
    final c = ComponentSettingsController(backend);
    await c.load();
    final save = c.save('tts', {'tts_provider': 'custom_audio:factory'});
    await Future<void>.delayed(Duration.zero);
    expect(backend.started, isTrue);
    c.dispose();
    expect(backend.closes, 0);
    backend.gate.complete();
    await save;
    await Future<void>.delayed(Duration.zero);
    expect(backend.closes, 1);
    expect(backend.revision, 1);
  });

  test('inspection acknowledges a lost successful reply without replay',
      () async {
    final backend = UncertainBackend();
    final c = ComponentSettingsController(backend);
    await c.load();
    c.draft('tts', {'tts_provider': 'kokoro'});
    c.edit('tts', {'tts_provider': 'custom_audio:factory'});
    await c.save('tts', c.drafts['tts']!.values);
    expect(c.inspectionRequired, isTrue);
    await c.load();
    expect(c.inspectionRequired, isFalse);
    expect(c.drafts['tts']!.dirty, isFalse);
    expect(c.status, contains('Inspection found'));
    expect(backend.saves, 1);
    c.dispose();
  });

  test('dirty drafts retain their reviewed baseline through refresh', () async {
    final backend = SavedComponents();
    final c = ComponentSettingsController(backend);
    await c.load();
    c.draft('tts', {'tts_provider': 'kokoro'});
    c.edit('tts', {'tts_provider': 'custom_audio:factory'});
    backend.revision = 7;
    await c.load();
    expect(c.drafts['tts']!.revision, '0');
    expect(c.drafts['tts']!.values['tts_provider'], 'custom_audio:factory');
    await c.save('tts', c.drafts['tts']!.values);
    expect(
        backend.requests
            .firstWhere((r) => r.parameters['action'] == 'review')
            .parameters['revision'],
        '0');
    c.dispose();
  });

  test('draft maps are scoped to the configuration owner', () async {
    final a = ComponentSettingsController(SavedComponents(), root: 'first');
    final b = ComponentSettingsController(SavedComponents(), root: 'second');
    await a.load();
    await b.load();
    a.draft('tts', {'tts_provider': 'kokoro'});
    a.edit('tts', {'tts_provider': 'custom_audio:factory'});
    expect(b.draft('tts', {'tts_provider': 'kokoro'}).dirty, isFalse);
    a.dispose();
    b.dispose();
  });

  testWidgets('admitted save continues when navigating away and reopening',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final backend = PendingBackend();
    await tester.pumpWidget(OdessyApp(api: DemoApi(), setupBackend: backend));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Text to speech (TTS)').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('component-tts_provider')),
        'custom_audio:factory');
    await tester.pump();
    await tester.ensureVisible(find.text('Save this section'));
    await tester.tap(find.text('Save this section'));
    await tester.pump();
    expect(backend.started, isTrue);
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')));
    await tester.pump();
    expect(backend.closes, 0);
    backend.gate.complete();
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Text to speech (TTS)').first);
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const ValueKey('component-tts_provider')))
            .controller!
            .text,
        'custom_audio:factory');
    expect(find.textContaining('Saved. Restart'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('dirty component navigation asks before losing input',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester
        .pumpWidget(OdessyApp(api: DemoApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Text to speech (TTS)').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('component-kokoro_model')),
        '/draft/model.onnx');
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')));
    await tester.pumpAndSettle();
    expect(find.text('Keep editing'), findsOneWidget);
    await tester.tap(find.text('Keep editing'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const ValueKey('component-kokoro_model')))
            .controller!
            .text,
        '/draft/model.onnx');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'section Discard and navigation Save affect only the reviewed component',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final backend = SavedComponents();
    await tester.pumpWidget(OdessyApp(api: DemoApi(), setupBackend: backend));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Text to speech (TTS)').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('component-tts_provider')),
        'custom_audio:factory');
    await tester.tap(find.text('Speech to text (STT)').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discard').last);
    await tester.pumpAndSettle();
    expect(backend.requests.where((r) => r.parameters['action'] == 'save'),
        isEmpty);
    await tester.tap(find.text('Text to speech (TTS)').first);
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const ValueKey('component-tts_provider')))
            .controller!
            .text,
        'kokoro');
    await tester.enterText(find.byKey(const ValueKey('component-tts_provider')),
        'custom_audio:factory');
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();
    expect(backend.requests.where((r) => r.parameters['action'] == 'save'),
        hasLength(1));
    expect((backend.selection['voice'] as Json)['tts_provider'],
        'custom_audio:factory');
    expect((backend.selection['voice'] as Json)['whisper_bin'], 'existing-stt');
    expect(backend.selection['model'], isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('collaboration edit is retained until explicit discard',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester
        .pumpWidget(OdessyApp(api: DemoApi(), setupBackend: SavedComponents()));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('consultation-limit')), '3');
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')));
    await tester.pumpAndSettle();
    expect(find.text('Unsaved collaboration setting'), findsOneWidget);
    await tester.tap(find.text('Keep editing'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('consultation-limit')))
            .controller!
            .text,
        '3');
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discard').last);
    await tester.pumpAndSettle();
    expect(find.text('Unsaved collaboration setting'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
