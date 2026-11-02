import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_history.dart';
import 'monitoring_history_test.dart';
import 'monitoring_presentation_test.dart';

void main() {
  testWidgets('render standalone history evidence with bundled fonts',
      (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final report = historyFixture();
    report['to'] = '2026-10-26T06:12:00Z';
    (report['series'] as List)[0]['points'] = [
      for (var minute = 0; minute < 12; minute++)
        if (minute != 3 && minute != 8)
          historyPoint(
              minute, [12, 19, 15, 0, 38, 43, 57, 49, 0, 31, 24, 18][minute],
              coverage: minute == 4 ? .4 : 1)
    ];
    Future<void> capture(String name, Brightness brightness,
        {bool narrow = false,
        bool degraded = false,
        bool denied = false}) async {
      tester.view.physicalSize =
          Size(narrow ? 390 : 1100, narrow ? 1000 : 1500);
      final key = GlobalKey();
      await tester.pumpWidget(RepaintBoundary(
          key: key,
          child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: odessyTheme(brightness),
              home: Scaffold(
                  body: MonitoringHistory(
                      host: monitoringHostFixture(
                          connection: degraded ? 'offline' : 'online',
                          access: denied ? 'denied' : 'allowed'),
                      report: report,
                      demo: true,
                      retained: degraded,
                      degradation: degraded
                          ? 'History storage is unavailable. Retained buckets may be incomplete.'
                          : null,
                      onRefresh: () {})))));
      await tester.pumpAndSettle();
      await tester.runAsync(() async {
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final frame = await boundary.toImage(pixelRatio: 1);
        final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/screenshots/history').create(recursive: true);
        await File('build/screenshots/history/$name.png')
            .writeAsBytes(bytes!.buffer.asUint8List());
        frame.dispose();
      });
      expect(tester.takeException(), isNull);
    }

    await capture('history-dark', Brightness.dark);
    await capture('history-light', Brightness.light);
    await capture('history-narrow', Brightness.dark, narrow: true);
    await capture('history-degraded', Brightness.light, degraded: true);
    await capture('history-denied', Brightness.dark, denied: true);
  });
}
