import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';

import 'setup_flow_fakes.dart';
import 'setup_usability_test.dart' show FakeAck, FakePicker;

/// Renders the setup surface at the sizes and Windows scaling settings named in
/// the usability plan. Artifacts land in `build/screenshots` (not committed).
void main() {
  testWidgets('capture setup surfaces at 720/960/1280', (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    for (final entry in const [
      ('720', Size(720, 540), 1.0),
      ('960', Size(960, 700), 1.0),
      ('1280-150', Size(1280, 800), 1.5),
    ]) {
      tester.view.physicalSize = entry.$2;
      tester.view.devicePixelRatio = entry.$3;
      final key = GlobalKey();
      final backend = FlowBackend();
      await tester.pumpWidget(RepaintBoundary(
          key: key,
          child: SetupApplication(
              mode: 'setup',
              backendFactory: () => backend,
              picker: FakePicker(),
              acknowledgement: FakeAck(true),
              onExit: () async {})));
      for (var i = 0; i < 8; i++) {
        await tester.pump(const Duration(milliseconds: 50));
      }
      for (final action in const ['Continue', 'Continue', 'Continue']) {
        await tester.tap(find.text(action));
        for (var i = 0; i < 8; i++) {
          await tester.pump(const Duration(milliseconds: 50));
        }
      }
      await tester.tap(find.text('Review this installation'));
      for (var i = 0; i < 8; i++) {
        await tester.pump(const Duration(milliseconds: 50));
      }
      expect(tester.takeException(), isNull,
          reason: 'no overflow or layout error at ${entry.$1}');
      await tester.runAsync(() async {
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 1);
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/screenshots').create(recursive: true);
        await File('build/screenshots/setup-review-${entry.$1}.png')
            .writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
    }
  });
}
