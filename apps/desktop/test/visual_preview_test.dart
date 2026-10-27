import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';

void main() {
  testWidgets('render desktop preview artifacts', (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final key = GlobalKey();
    await tester
        .pumpWidget(RepaintBoundary(key: key, child: const OdessyApp()));
    await tester.pumpAndSettle();
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final image = await boundary.toImage(pixelRatio: 1);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/preview').create(recursive: true);
      await File('build/preview/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    }

    await tester.runAsync(() => capture('conversation-dark'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('conversation-light'));
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('device-light'));
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-light'));
    await tester.tap(find.text('Text to speech (TTS)'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-tts-light'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-tts-dark'));
  });
}
