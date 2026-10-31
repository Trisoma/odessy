import 'package:flutter/material.dart';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/settings/controllers/component_settings_controller.dart';
import 'package:odessy_desktop/features/settings/presentation/component_settings_pane.dart';
import 'component_settings_test.dart' show SavedComponents;

bool focusedInside(Finder finder) {
  final focusContext = FocusManager.instance.primaryFocus?.context;
  if (focusContext == null) return false;
  return find
      .ancestor(
          of: find.byElementPredicate((e) => identical(e, focusContext)),
          matching: finder)
      .evaluate()
      .isNotEmpty;
}

void main() {
  setUpAll(() async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
  });
  test('reverting and discarding edits clears the stale unsaved status',
      () async {
    final controller = ComponentSettingsController(SavedComponents());
    addTearDown(controller.dispose);
    await controller.load();
    controller.activeSection = 'tts';
    controller.draft('tts', {'tts_provider': 'kokoro'});
    controller.edit('tts', {'tts_provider': 'custom_audio:factory'});
    expect(controller.status, 'Unsaved changes.');
    controller.edit('tts', {'tts_provider': 'kokoro'});
    expect(controller.dirty, isFalse);
    expect(controller.status, isEmpty);
    controller.edit('tts', {'tts_provider': 'custom_audio:factory'});
    controller.discard('tts');
    expect(controller.dirty, isFalse);
    expect(controller.status, isEmpty);
  });

  for (final brightness in Brightness.values) {
    testWidgets('Browse has visible text in $brightness with enlarged text',
        (tester) async {
      tester.view.physicalSize = const Size(840, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final controller = ComponentSettingsController(SavedComponents());
      addTearDown(controller.dispose);
      final capture = GlobalKey();
      await tester.pumpWidget(MaterialApp(
          theme: odessyTheme(brightness),
          home: MediaQuery(
              data: const MediaQueryData(textScaler: TextScaler.linear(2)),
              child: RepaintBoundary(
                  key: capture,
                  child: Scaffold(
                      body: SingleChildScrollView(
                          child: ComponentSettingsPane(
                              controller: controller, section: 'tts')))))));
      await tester.pumpAndSettle();
      expect(find.text('Browse…'), findsNWidgets(2));
      expect(find.byTooltip('Browse kokoro model'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.runAsync(() async {
        final boundary = capture.currentContext!.findRenderObject()!
            as RenderRepaintBoundary;
        final image = await boundary.toImage();
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/preview/gui-audit').create(recursive: true);
        await File('build/preview/gui-audit/browse-${brightness.name}-200.png')
            .writeAsBytes(data!.buffer.asUint8List());
        image.dispose();
      });
      await tester.pumpWidget(const SizedBox());
    });
  }

  for (final width in [1280.0, 840.0]) {
    testWidgets(
        'dirty navigation autofocuses safe action and restores focus at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
          OdessyApp(api: DemoApi(), setupBackend: SavedComponents()));
      await tester.pumpAndSettle();
      Future<void> openNavigation() async {
        if (width < 900) {
          await tester.tap(find.byTooltip('Open navigation menu'));
          await tester.pumpAndSettle();
        }
      }

      await openNavigation();
      await tester.tap(find.byKey(const ValueKey('nav-Workspaces')));
      await tester.pumpAndSettle();
      final original = tester
          .widget<TextField>(find.byKey(const ValueKey('component-workspace')))
          .controller!
          .text;
      await tester.enterText(
          find.byKey(const ValueKey('component-workspace')), '/draft');
      await openNavigation();
      final trigger = find.byKey(const ValueKey('nav-Settings'));
      await tester.tap(trigger);
      await tester.pumpAndSettle();
      final keep = find.widgetWithText(TextButton, 'Keep editing');
      expect(focusedInside(keep), isTrue);
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pump();
      expect(focusedInside(find.widgetWithText(TextButton, 'Discard')), isTrue);
      await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
      await tester.pump();
      expect(focusedInside(keep), isTrue);
      await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
      await tester.pump();
      expect(focusedInside(find.widgetWithText(FilledButton, 'Save')), isTrue);
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pump();
      expect(focusedInside(keep), isTrue);
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pumpAndSettle();
      expect(find.text('Unsaved changes'), findsNothing);
      expect(
          tester.widget<ListTile>(trigger).focusNode?.hasPrimaryFocus, isTrue);
      expect(
          tester
              .widget<TextField>(
                  find.byKey(const ValueKey('component-workspace')))
              .controller!
              .text,
          '/draft');
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();
      expect(focusedInside(keep), isTrue);
      await tester.tap(keep);
      await tester.pumpAndSettle();
      expect(
          tester.widget<ListTile>(trigger).focusNode?.hasPrimaryFocus, isTrue);
      if (width < 900) {
        tester.state<ScaffoldState>(find.byType(Scaffold).first).closeDrawer();
        await tester.pumpAndSettle();
      }
      await tester.enterText(
          find.byKey(const ValueKey('component-workspace')), original);
      await tester.pumpAndSettle();
      expect(find.text('Unsaved changes.'), findsNothing);
      expect(find.text('No unsaved changes in this section.'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
