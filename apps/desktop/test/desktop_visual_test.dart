import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'component_settings_test.dart' show SavedComponents;

void main() {
  testWidgets('render desktop screenshots', (tester) async {
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
    await tester.pumpWidget(RepaintBoundary(
        key: key,
        child: OdessyApp(api: DemoApi(), setupBackend: SavedComponents())));
    await tester.pumpAndSettle();
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final image = await boundary.toImage(pixelRatio: 1);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/screenshots').create(recursive: true);
      await File('build/screenshots/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    }

    await tester.runAsync(() => capture('conversation-dark'));
    tester.view.physicalSize = const Size(1200, 480);
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('sidebar-short-dark'));
    tester.view.physicalSize = const Size(1200, 850);
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('conversation-light'));
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('device-light'));
    await tester.tap(find.widgetWithText(OutlinedButton, 'History'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('integrated-history-light'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('integrated-history-dark'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-light'));
    await tester.tap(
        find.byKey(const ValueKey('settings-section-Models and connections')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('consultation-limit')));
    await tester.enterText(find.byKey(const Key('consultation-limit')), '4');
    await tester.pump();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-leave-light'));
    await tester.tap(find.widgetWithText(TextButton, 'Keep editing'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('settings-leave-dark'));
    await tester.tap(find.widgetWithText(TextButton, 'Discard'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Tools & installation'));
    await tester.tap(find.text('Tools & installation'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Installation'), 160,
        scrollable: find.descendant(
            of: find.byKey(const Key('device-sidebar-scroll')),
            matching: find.byType(Scrollable)));
    await tester.drag(
        find.byKey(const Key('device-sidebar-scroll')), const Offset(0, -220));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Installation'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Install Odessy'));
    await tester.tap(find.text('Install Odessy'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('setup-welcome-light'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('setup-welcome-dark'));
  });
}
