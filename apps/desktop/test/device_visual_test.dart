import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/devices.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  testWidgets('render device screenshots in both themes', (
    tester,
  ) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader(
      'MaterialIcons',
    )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.physicalSize = const Size(1376, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final key = GlobalKey();
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final frame = await boundary.toImage(pixelRatio: 1);
      final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/screenshots/devices').create(recursive: true);
      await File('build/screenshots/devices/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      frame.dispose();
    }

    await tester.pumpWidget(
      RepaintBoundary(key: key, child: OdessyApp(api: DemoApi())),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('overview-dark'));
    final deviceSettings = find.descendant(
      of: find.byType(DevicePage),
      matching: find.text('Settings'),
    );
    await tester.tap(deviceSettings);
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-dark'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-light'));
    await tester.tap(find.text('Overview'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('overview-light'));

    final controller = DesktopSession(DemoApi());
    addTearDown(controller.dispose);
    await controller.start();
    tester.view.physicalSize = const Size(390, 844);
    await tester.pumpWidget(
      RepaintBoundary(
        key: key,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: odessyTheme(Brightness.dark),
          home: Scaffold(
            body: DevicePage(device: 'Jetson', controller: controller),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('overview-narrow'));
    expect(tester.takeException(), isNull);
  });
}
