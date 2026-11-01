import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';
import 'settings_presentation_test.dart';
import 'component_settings_test.dart' show SavedComponents;

void main() {
  testWidgets('render global settings screenshots', (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.physicalSize = const Size(1440, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final key = GlobalKey();
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final frame = await boundary.toImage(pixelRatio: 1);
      final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/screenshots/settings').create(recursive: true);
      await File('build/screenshots/settings/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      frame.dispose();
    }

    await tester.pumpWidget(
        RepaintBoundary(key: key, child: OdessyApp(api: SettingsFixtureApi(), setupBackend: SavedComponents())));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('general-dark'));
    await selectSettingsSection(tester, SettingsSections.models);
    await tester.runAsync(() => capture('models-dark'));
    await editLimit(tester, '33');
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('validation-dark'));
    await editLimit(tester, '4');
    await tester.ensureVisible(saveSettingsButton);
    await tester.tap(saveSettingsButton);
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('saved-dark'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('models-light'));
    await selectSettingsSection(tester, SettingsSections.advanced);
    await tester.runAsync(() => capture('advanced-light'));
    await selectSettingsSection(tester, SettingsSections.devices);
    await tester.runAsync(() => capture('devices-light'));

    final controller = DesktopSession(SettingsFixtureApi());
    addTearDown(controller.dispose);
    tester.view.physicalSize = const Size(390, 1100);
    await tester.pumpWidget(RepaintBoundary(
        key: key,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: odessyTheme(Brightness.dark),
          home: Scaffold(body: SettingsPage(controller: controller)),
        )));
    await tester.pumpAndSettle();
    await selectSettingsSection(tester, SettingsSections.models);
    await tester.runAsync(() => capture('models-narrow'));
    expect(tester.takeException(), isNull);
  });
}
