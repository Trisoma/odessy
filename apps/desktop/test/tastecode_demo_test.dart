import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/ui_demo/presentation/demo_workspace.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart'
    show SettingsSections;
import 'package:odessy_desktop/widgets/pixel_boat.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('OdessyMono')
          ..addFont(rootBundle.load('assets/fonts/roboto-mono.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
  });
  Future<void> start(WidgetTester tester,
      {Size size = const Size(1600, 1050), GlobalKey? boundary}) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
        RepaintBoundary(key: boundary, child: const TastecodeDemo()));
    await tester.pumpAndSettle();
  }

  testWidgets(
      'session navigation, filtering, collapse, and new session are isolated',
      (tester) async {
    await start(tester);
    await tester.tap(find.text('Understand the workspace'));
    await tester.pumpAndSettle();
    expect(find.textContaining('The fixture workspace has'), findsOneWidget);
    await tester.enterText(
        find.widgetWithText(TextField, 'Find a session'), 'permission');
    await tester.pumpAndSettle();
    expect(find.text('Review collector reconnect'), findsNothing);
    expect(find.text('Trace permission boundaries'), findsOneWidget);
    await tester.tap(find.byTooltip('Collapse sidebar'));
    await tester.pumpAndSettle();
    expect(find.text('Find a session'), findsNothing);
    await tester.tap(find.byTooltip('Open navigation'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('New session'));
    await tester.pumpAndSettle();
    expect(find.text('What would you like to explore?'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'activity, approval, files, terminal, and diffs stay explicit fixtures',
      (tester) async {
    await start(tester);
    await tester.tap(find.byKey(const ValueKey('activity-toggle')));
    await tester.pumpAndSettle();
    expect(find.text('Live verification unavailable'), findsOneWidget);
    await tester.tap(find.byTooltip('Open terminal'));
    await tester.pumpAndSettle();
    expect(find.text('Illustrative transcript, not an executed check.'),
        findsOneWidget);
    await tester.tap(find.byTooltip('Open changes'));
    await tester.pumpAndSettle();
    expect(find.text('+     await self.stop_reader()'), findsOneWidget);
    await tester.tap(find.byTooltip('Open files'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('test_collector.py'));
    await tester.pumpAndSettle();
    expect(find.text('async def test_reconnect_has_one_reader():'),
        findsOneWidget);
    final approve = find.text('Approve demo');
    await tester.ensureVisible(approve);
    await tester.pumpAndSettle();
    await tester.tap(approve);
    await tester.pumpAndSettle();
    expect(find.text('Approved · nothing applied'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'composer supports focus, engine fixture, attachment, simulated voice and stop',
      (tester) async {
    await start(tester);
    await tester
        .tap(find.byTooltip('Choose demo model and engine (next turn)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Qwen3 / Harness · fixture'));
    await tester.pumpAndSettle();
    expect(find.text('Qwen3 / Harness'), findsOneWidget);
    await tester.tap(find.byTooltip('Attach fixture file'));
    await tester.pumpAndSettle();
    expect(find.text('collector.py · fixture'), findsOneWidget);
    await tester.tap(find.byTooltip('Simulate voice state (no microphone)'));
    await tester.pumpAndSettle();
    expect(find.text('Simulated listening · microphone is not accessed'),
        findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('demo-composer')));
    expect(FocusManager.instance.primaryFocus?.debugLabel, 'Demo composer');
    await tester.enterText(
        find.byKey(const ValueKey('demo-composer')), 'Review the fixture');
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.stop_rounded), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('send-stop')));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.arrow_upward), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('demo-composer')));
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pumpAndSettle();
    expect(FocusManager.instance.primaryFocus, isNot(composerFocusFor(tester)));
    expect(tester.takeException(), isNull);
  });

  testWidgets('narrow navigation and workspace drawers work without overflow',
      (tester) async {
    await start(tester, size: const Size(720, 1000));
    await tester.tap(find.byTooltip('Open navigation'));
    await tester.pumpAndSettle();
    expect(find.text('Computers · fixtures'), findsOneWidget);
    await tester.ensureVisible(find.text('Trace permission boundaries'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Trace permission boundaries'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Open terminal'));
    await tester.pumpAndSettle();
    expect(find.text('Recorded output'), findsOneWidget);
    await tester.tap(find.byTooltip('Close workspace panel'));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('SSH authentication establishes'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('compact width and enlarged text remain usable', (tester) async {
    await start(tester, size: const Size(480, 900));
    expect(tester.takeException(), isNull);
    tester.platformDispatcher.textScaleFactorTestValue = 1.4;
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    tester.view.physicalSize = const Size(720, 1100);
    await tester.pumpWidget(const TastecodeDemo());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('capture rendered review screenshots', (tester) async {
    if (!const bool.fromEnvironment('CAPTURE_UI_DEMO')) return;
    final boundary = GlobalKey();
    await start(tester, boundary: boundary);
    Future<void> capture(String name) async {
      expect(tester.takeException(), isNull);
      await tester.runAsync(() async {
        final render = boundary.currentContext!.findRenderObject()!
            as RenderRepaintBoundary;
        final image = await render.toImage(pixelRatio: 1);
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        const output = String.fromEnvironment('UI_DEMO_OUTPUT',
            defaultValue: '../../../view/ui/tastecode-demo');
        await Directory(output).create(recursive: true);
        await File('$output/$name.png')
            .writeAsBytes(data!.buffer.asUint8List());
        image.dispose();
      });
    }

    await capture('01-assistant-files');
    await tester.tap(find.byKey(const ValueKey('activity-toggle')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Approve demo'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Open changes'));
    await tester.pumpAndSettle();
    await capture('02-activity-approval-diff');
    await tester.tap(find.byTooltip('Open terminal'));
    await tester.pumpAndSettle();
    await capture('03-terminal');
    await tester.tap(find.byTooltip('Open files'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('test_collector.py'));
    await tester.tap(find.byTooltip('Simulate voice state (no microphone)'));
    await tester.pumpAndSettle();
    await capture('04-file-voice');
    tester.view.physicalSize = const Size(720, 1000);
    await tester.pumpAndSettle();
    await capture('05-narrow-assistant');
    await tester.tap(find.byTooltip('Open navigation'));
    await tester.pumpAndSettle();
    await capture('06-narrow-sidebar');
    await tester.tap(find.byTooltip('Close navigation'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Open changes'));
    await tester.pumpAndSettle();
    await capture('07-narrow-diff');
    await tester.tap(find.byTooltip('Close workspace panel'));
    await tester.pumpAndSettle();
    tester.view.physicalSize = const Size(1600, 1050);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ListTile, 'Workspaces'));
    await tester.pumpAndSettle();
    await capture('08-workspaces');
    await tester.tap(find.widgetWithText(ListTile, 'Devices'));
    await tester.pumpAndSettle();
    await capture('09-devices-overview');
    await tester.tap(find.widgetWithText(ChoiceChip, 'Performance'));
    await tester.pumpAndSettle();
    await capture('10-devices-performance');
    await tester.tap(find.text('Fresh sample'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Permission denied sample').last);
    await tester.pumpAndSettle();
    await capture('11-devices-permission-denied');
    await tester.tap(find.widgetWithText(ListTile, 'Settings'));
    await tester.pumpAndSettle();
    final names = [
      'general',
      'models',
      'stt',
      'tts',
      'voice',
      'tool-access',
      'advanced',
      'installation'
    ];
    for (var i = 0; i < SettingsSections.all.length; i++) {
      await tester
          .tap(find.byKey(ValueKey('settings-${SettingsSections.all[i]}')));
      await tester.pumpAndSettle();
      await capture('${12 + i}-settings-${names[i]}');
    }
    tester.view.physicalSize = const Size(720, 1000);
    await tester.pumpAndSettle();
    await capture('20-settings-narrow');
    tester.view.physicalSize = const Size(1600, 1050);
    await tester.pumpAndSettle();
    for (final entry in [
      ('Activity & usage', '21-activity-usage'),
      ('Jobs', '22-jobs'),
      ('Handoffs', '23-handoffs'),
    ]) {
      await tester.tap(find.widgetWithText(ListTile, entry.$1));
      await tester.pumpAndSettle();
      await capture(entry.$2);
    }
    await tester.tap(find.byTooltip('Tools and recovery'));
    await tester.pumpAndSettle();
    await capture('24-tools-recovery-navigation');
    await tester.tap(find.text('Runtime maintenance'));
    await tester.pumpAndSettle();
    await capture('25-retained-maintenance');
  });

  testWidgets(
      'all demo routes and settings sections render and preserve chat draft',
      (tester) async {
    await start(tester);
    expect(find.byType(PixelBoat), findsNWidgets(2));
    expect(find.byIcon(Icons.auto_awesome), findsNothing);
    await tester.enterText(
        find.byKey(const ValueKey('demo-composer')), 'Keep this draft');
    for (final route in [
      'Workspaces',
      'Devices',
      'Activity & usage',
      'Jobs',
      'Handoffs',
      'Settings'
    ]) {
      await tester.tap(find.widgetWithText(ListTile, route));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    }
    for (final section in SettingsSections.all) {
      await tester.tap(find.byKey(ValueKey('settings-$section')));
      await tester.pumpAndSettle();
      expect(find.byKey(ValueKey('settings-content-$section')), findsOneWidget);
      expect(tester.takeException(), isNull);
    }
    await tester.tap(find.widgetWithText(ListTile, 'Assistant'));
    await tester.pumpAndSettle();
    expect(find.text('Keep this draft'), findsOneWidget);
    await tester.tap(find.widgetWithText(ListTile, 'Workspaces'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open sample conversation'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('demo-composer')), findsOneWidget);
  });

  testWidgets(
      'devices hide readings on denial and mark disconnected samples stale',
      (tester) async {
    await start(tester);
    await tester.tap(find.widgetWithText(ListTile, 'Devices'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ChoiceChip, 'Performance'));
    await tester.pumpAndSettle();
    expect(find.text('23%'), findsOneWidget);
    await tester.tap(find.text('Fresh sample'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Permission denied sample').last);
    await tester.pumpAndSettle();
    expect(find.text('23%'), findsNothing);
    expect(find.text('Telemetry is unavailable'), findsOneWidget);
    await tester.tap(find.text('Permission denied sample').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Disconnected sample').last);
    await tester.pumpAndSettle();
    expect(find.text('Stale sample · no live collector'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('new pages support narrow navigation and scaled settings',
      (tester) async {
    await start(tester, size: const Size(720, 1050));
    for (final route in ['Workspaces', 'Devices', 'Settings']) {
      await tester.tap(find.byTooltip('Open navigation'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(ListTile, route));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    }
    tester.platformDispatcher.textScaleFactorTestValue = 1.4;
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'handoff fixture separates steps, output review and explicit decision',
      (tester) async {
    await start(tester);
    await tester.tap(find.widgetWithText(ListTile, 'Handoffs'));
    await tester.pumpAndSettle();
    expect(find.text('Write a focused plan'), findsOneWidget);
    expect(find.text('Implement the plan'), findsOneWidget);
    await tester.tap(find.text('Inspect sample output'));
    await tester.pumpAndSettle();
    expect(find.textContaining('1. Join the old collector.'), findsOneWidget);
    await tester.tap(find.text('Approve demo transfer'));
    await tester.pumpAndSettle();
    expect(find.text('Transfer reviewed · fixture only'), findsOneWidget);
    await tester.tap(find.widgetWithText(ChoiceChip, 'Implement then review'));
    await tester.pumpAndSettle();
    expect(find.text('Review the result'), findsOneWidget);
    tester.view.physicalSize = const Size(720, 1000);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}

FocusNode? composerFocusFor(WidgetTester tester) => tester
    .widget<TextField>(find.byKey(const ValueKey('demo-composer')))
    .focusNode;
