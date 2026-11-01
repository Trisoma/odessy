import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/services.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/settings/controllers/component_settings_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/component_settings_pane.dart';

class SavedComponents implements SetupBackend {
  Json selection = {
    'role': 'combined',
    'model': null,
    'voice': <String, dynamic>{
      'enabled': false,
      'tts_provider': 'kokoro',
      'stt_provider': 'whisper.cpp',
      'whisper_bin': 'existing-stt'
    }
  };
  final requests = <SetupRequest>[];
  int revision = 0;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    final p = request.parameters;
    if (p['action'] == 'save') {
      expect(p['section'], 'tts');
      (selection['voice'] as Json).addAll(p['patch'] as Json);
      revision++;
    }
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'selection': selection,
        'revision': '$revision',
        'plan_id': 'review-$revision',
        'saved': p['action'] == 'save'
      }
    }, request.id);
  }

  @override
  Future<void> close() async {}
  @override
  Future<void> cancel() async {}
}

void main() {
  testWidgets('TTS saves and reopens without configuring LLM STT or host',
      (tester) async {
    tester.view.physicalSize = const Size(1100, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final backend = SavedComponents();
    var c = ComponentSettingsController(backend);
    Future<void> open() async {
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child:
                      ComponentSettingsPane(controller: c, section: 'tts')))));
      await tester.pumpAndSettle();
    }

    await open();
    await tester.enterText(find.byKey(const ValueKey('component-kokoro_model')),
        '/existing/kokoro.onnx');
    await tester.enterText(
        find.byKey(const ValueKey('component-kokoro_voices')),
        '/existing/voices.bin');
    await tester.ensureVisible(find.text('Save this section'));
    await tester.tap(find.text('Save this section'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Saved. Restart'), findsOneWidget,
        reason: '${c.error} / ${c.status}');
    expect(backend.selection['model'], isNull);
    expect((backend.selection['voice'] as Json)['whisper_bin'], 'existing-stt');
    await tester.pumpWidget(const SizedBox());
    c.dispose();
    c = ComponentSettingsController(backend);
    await open();
    expect(
        tester
            .widget<TextField>(
                find.byKey(const ValueKey('component-kokoro_model')))
            .controller!
            .text,
        '/existing/kokoro.onnx');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });
  testWidgets('capture independent TTS settings surface', (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.physicalSize = const Size(1000, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final c = ComponentSettingsController(SavedComponents());
    final key = GlobalKey();
    await tester.pumpWidget(RepaintBoundary(
        key: key,
        child: MaterialApp(
            theme: odessyTheme(Brightness.dark),
            home: Scaffold(
                body: Padding(
                    padding: const EdgeInsets.all(28),
                    child: ComponentSettingsPane(
                        controller: c, section: 'tts'))))));
    await tester.pumpAndSettle();
    await tester.runAsync(() async {
      final image = await (key.currentContext!.findRenderObject()!
              as RenderRepaintBoundary)
          .toImage();
      final data = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/preview').create(recursive: true);
      await File('build/preview/independent-tts-settings.png')
          .writeAsBytes(data!.buffer.asUint8List());
      image.dispose();
    });
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });
}
