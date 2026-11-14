import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/conversation/presentation/usage_history_dashboard.dart';

void main() {
  test('30-day trend includes every UTC day and keeps missing days distinct',
      () {
    final days = usageTimeline([
      {'day': '2026-11-05', 'requests': 1, 'prompt': 10, 'completion': 5},
    ], 30, DateTime.utc(2026, 11, 5, 23));
    expect(days.length, 30);
    expect(days.first['day'], '2026-10-07');
    expect(days.last['day'], '2026-11-05');
    expect(days.first['recorded'], false);
    expect(days.last['requests'], 1);
    expect(usageTimeline(const [], 7, DateTime.utc(2026, 11, 5)).length, 7);
    expect(usageTimeline(const [], 90, DateTime.utc(2026, 11, 5)).length, 90);
  });

  testWidgets(
      'a single request uses the full 30-day axis and explains missing cost',
      (tester) async {
    tester.view.physicalSize = const Size(1440, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(api: SingleUsageApi()));
    await tester.pumpAndSettle();
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    final now = DateTime.now().toUtc();
    final today = DateTime.utc(now.year, now.month, now.day);
    final start = today
        .subtract(const Duration(days: 29))
        .toIso8601String()
        .substring(0, 10);
    final end = today.toIso8601String().substring(0, 10);
    expect(find.text(start), findsOneWidget);
    expect(find.text(end), findsOneWidget);
    expect(find.byTooltip('$start: No recorded requests'), findsOneWidget);
    expect(find.text('Price, tokens or currency unavailable'), findsOneWidget);
  });
  testWidgets('usage preview filters history and returns to conversation',
      (tester) async {
    tester.view.physicalSize = const Size(1440, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(api: UsagePreviewApi()));
    await tester.pumpAndSettle();
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    expect(find.text('Usage overview'), findsOneWidget);
    expect(find.textContaining('Demo data'), findsWidgets);
    expect(find.text('Usage trend'), findsOneWidget);
    expect(find.text('Request history'), findsOneWidget);
    expect(find.text('deepseek'), findsWidgets);
    await tester.ensureVisible(find.text('deepseek').first);
    await tester.tap(find.text('deepseek').first);
    await tester.pumpAndSettle();
    expect(find.text('Usage report'), findsOneWidget);
    await tester.tap(find.text('Close'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Providers'));
    await tester.tap(find.text('Providers'));
    await tester.pumpAndSettle();
    expect(find.text('Showing up to the top 100 providers by request count.'),
        findsOneWidget);
    expect(find.text('Models'), findsOneWidget);
    await tester.ensureVisible(find.text('Last 30 days'));
    await tester.tap(find.text('Last 30 days'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Last 7 days').last);
    await tester.pumpAndSettle();
    expect(find.text('Last 7 days'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('nav-Assistant')).hitTestable());
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('composer')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('dashboard is readable in both themes and a scaled narrow drawer',
      (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.physicalSize = const Size(1440, 1050);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final key = GlobalKey();
    await tester.pumpWidget(
        RepaintBoundary(key: key, child: OdessyApp(api: UsagePreviewApi())));
    await tester.pumpAndSettle();
    await tester
        .tap(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable());
    await tester.pumpAndSettle();
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final frame = await boundary.toImage(pixelRatio: 1);
      final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/screenshots/usage').create(recursive: true);
      await File('build/screenshots/usage/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      frame.dispose();
    }

    await tester.runAsync(() => capture('dark'));
    await tester.tap(find.byTooltip('Toggle light / dark theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('light'));
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pump();
    expect(FocusManager.instance.primaryFocus, isNotNull);
    tester.view.physicalSize = const Size(800, 700);
    tester.platformDispatcher.textScaleFactorTestValue = 1.5;
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Open navigation menu'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('nav-Activity & usage')).hitTestable(),
        findsWidgets);
    await tester.tap(
        find.byKey(const ValueKey('nav-Activity & usage')).hitTestable().last);
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('narrow-large-text'));
    expect(find.text('Usage overview'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

class SingleUsageApi extends UsagePreviewApi {
  @override
  Future<Json> usageHistory(
      {int days = 30,
      String? provider,
      String? model,
      int limit = 50,
      int offset = 0}) async {
    final day = DateTime.now().toUtc().toIso8601String().substring(0, 10);
    return {
      'schema_version': 1,
      'summary': {
        'requests': 1,
        'prompt': 800,
        'cached': 768,
        'completion': 139,
        'cost': null,
        'currency': null
      },
      'daily': [
        {
          'day': day,
          'requests': 1,
          'prompt': 800,
          'cached': 768,
          'completion': 139,
          'cost': null,
          'currency': null
        }
      ],
      'groups': <Json>[],
      'providers': <Json>[],
      'options': <Json>[],
      'requests': <Json>[],
      'total': 1,
      'limit': limit,
      'offset': offset,
    };
  }
}
