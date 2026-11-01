import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_history.dart';
import 'monitoring_presentation_test.dart';

Json historyPoint(int minute, num value, {num coverage = 1}) => {
      'at': '2026-10-26T06:${minute.toString().padLeft(2, '0')}:00Z',
      'value': value,
      'min': value,
      'max': value,
      'count': 6,
      'coverage': coverage,
    };

Json historyFixture({String host = 'host_a'}) => {
      'schema_version': 1,
      'host_id': host,
      'from': '2026-10-26T06:00:00Z',
      'to': '2026-10-26T06:04:00Z',
      'step_seconds': 60,
      'series': [
        <String, dynamic>{
          'name': 'cpu.utilization_percent',
          'points': [
            historyPoint(0, 0),
            historyPoint(1, 40),
            historyPoint(3, 80)
          ]
        },
        <String, dynamic>{
          'name': 'memory.used_bytes',
          'points': [historyPoint(0, 9 * 1024 * 1024 * 1024)]
        }
      ]
    };

Future<void> historyPage(WidgetTester tester,
        {Json? report,
        MonitoringHost? host,
        bool loading = false,
        bool retained = false,
        String? error,
        String? degradation,
        VoidCallback? onRefresh,
        Size size = const Size(1100, 1800),
        Brightness brightness = Brightness.dark,
        double scale = 1}) =>
    mountMonitoring(
        tester,
        MonitoringHistory(
            host: host ?? monitoringHostFixture(),
            report: report,
            loading: loading,
            retained: retained,
            error: error,
            degradation: degradation,
            demo: true,
            onRefresh: onRefresh),
        settle: !loading,
        size: size,
        brightness: brightness,
        scale: scale);

Future<void> chooseMemory(WidgetTester tester) async {
  await tester.ensureVisible(find.byKey(const Key('history-metric')));
  await tester.tap(find.byKey(const Key('history-metric')));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Memory used').last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('no host, missing response and loading remain explicit',
      (tester) async {
    await mountMonitoring(tester, const MonitoringHistory());
    expect(find.text('No device selected'), findsOneWidget);
    await historyPage(tester);
    expect(find.text('History unavailable'), findsOneWidget);
    expect(find.byKey(const Key('history-plot')), findsNothing);
    await historyPage(tester, loading: true);
    expect(find.text('Loading history'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
  });

  testWidgets('reported values, bucket metadata and UTC range stay distinct',
      (tester) async {
    await historyPage(tester, report: historyFixture());
    expect(find.text('DEMO · illustrative history data'), findsOneWidget);
    expect(find.text('3 plotted of 3 returned buckets'), findsOneWidget);
    expect(find.text('80.0 %'), findsOneWidget);
    expect(find.text('80.0 % / 80.0 %'), findsOneWidget);
    expect(find.text('100.0%'), findsOneWidget);
    expect(find.text('6'), findsOneWidget);
    expect(find.text('Vertical scale: 0 to 100.0 %'), findsOneWidget);
    expect(find.textContaining('Whole buckets may overlap'), findsOneWidget);
  });

  testWidgets('zero observations stay zero and partial coverage is reported',
      (tester) async {
    final report = historyFixture();
    (report['series'] as List)[0]
        ['points'] = [historyPoint(0, 0, coverage: .25)];
    await historyPage(tester, report: report);
    expect(find.text('0.0 %'), findsOneWidget);
    expect(find.text('25.0%'), findsOneWidget);
    expect(find.text('1 plotted of 1 returned buckets'), findsOneWidget);
  });

  for (final access in ['denied', 'disabled', 'unsupported', 'revoked']) {
    testWidgets('$access hides supplied telemetry', (tester) async {
      await historyPage(tester,
          report: historyFixture(),
          host: monitoringHostFixture(
              trust: access == 'revoked' ? 'revoked' : 'paired',
              access: access == 'revoked' ? 'allowed' : access),
          onRefresh: () => fail('Restricted history must not refresh'));
      expect(find.text('History unavailable'), findsOneWidget);
      expect(find.byKey(const Key('history-metric')), findsNothing);
      expect(find.text('80.0 %'), findsNothing);
      expect(
          tester
              .widget<OutlinedButton>(
                  find.widgetWithText(OutlinedButton, 'Refresh history'))
              .onPressed,
          isNull);
    });
  }

  for (final mismatch in ['host_id', 'schema_version']) {
    testWidgets('$mismatch mismatch hides the complete report', (tester) async {
      final report = historyFixture();
      report[mismatch] = mismatch == 'host_id' ? 'host_other' : 2;
      await historyPage(tester, report: report);
      expect(find.text('History report unavailable'), findsOneWidget);
      expect(find.byKey(const Key('history-plot')), findsNothing);
    });
  }

  for (final invalid in <String, Json>{
    'numeric string': {'value': '33'},
    'nonfinite value': {'value': double.nan},
    'out of range': {'value': 101},
    'missing value': {'value': null},
    'zero count': {'count': 0},
    'invalid coverage': {'coverage': 1.1},
    'reversed range': {'min': 90, 'max': 20},
  }.entries) {
    testWidgets('${invalid.key} is unavailable, not an invented zero',
        (tester) async {
      final report = historyFixture();
      (report['series'] as List)[0]
          ['points'] = [historyPoint(0, 40)..addAll(invalid.value)];
      await historyPage(tester, report: report);
      expect(find.text('Invalid buckets'), findsOneWidget);
      expect(find.text('0 plotted of 1 returned buckets'), findsOneWidget);
      expect(find.text('Unavailable'), findsWidgets);
      expect(find.text('0.0 %'), findsNothing);
    });
  }

  testWidgets(
      'nil bucket arrays and storage degradation do not imply zero activity',
      (tester) async {
    final report = historyFixture();
    (report['series'] as List)[0]['points'] = null;
    await historyPage(tester,
        report: report, degradation: 'History write failed');
    expect(find.text('History storage degraded'), findsOneWidget);
    expect(find.text('History may be incomplete'), findsOneWidget);
    expect(find.byKey(const Key('history-plot')), findsNothing);
    expect(
        find.textContaining('not evidence of zero activity'), findsOneWidget);
  });

  testWidgets('unknown series do not acquire inferred units', (tester) async {
    final report = historyFixture();
    report['series'] = [
      {
        'name': 'cpu.load_1',
        'points': [historyPoint(0, 2)]
      }
    ];
    await historyPage(tester, report: report);
    expect(find.text('No supported history series'), findsOneWidget);
    expect(find.text('Unsupported series'), findsOneWidget);
    expect(find.byKey(const Key('history-plot')), findsNothing);
  });

  testWidgets(
      'selection converts bytes to binary GiB without inventing utilization',
      (tester) async {
    await historyPage(tester, report: historyFixture());
    await chooseMemory(tester);
    expect(find.text('9.00 GiB'), findsOneWidget);
    expect(find.text('Memory used · GiB'), findsOneWidget);
    expect(find.text('Vertical scale: 0 to 9.00 GiB'), findsOneWidget);
  });

  testWidgets(
      'point controls and arrow keys inspect sorted original bucket times',
      (tester) async {
    final report = historyFixture();
    (report['series'] as List)[0]['points'] = [
      historyPoint(3, 80),
      historyPoint(0, 0),
      historyPoint(1, 40)
    ];
    await historyPage(tester, report: report);
    await tester.tap(find.widgetWithText(OutlinedButton, 'Previous bucket'));
    await tester.pumpAndSettle();
    expect(find.text('Bucket 2 of 3'), findsOneWidget);
    expect(find.text('40.0 %'), findsOneWidget);
    Focus.of(tester.element(find.byKey(const Key('history-plot'))))
        .requestFocus();
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowLeft);
    await tester.pumpAndSettle();
    expect(find.text('Bucket 1 of 3'), findsOneWidget);
    expect(find.text('0.0 %'), findsOneWidget);
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
    await tester.pumpAndSettle();
    expect(find.text('Bucket 2 of 3'), findsOneWidget);
  });

  testWidgets(
      'whole overlapping first bucket is preserved before requested start',
      (tester) async {
    final report = historyFixture()..['from'] = '2026-10-26T06:00:20Z';
    (report['series'] as List)[0]['points'] = [historyPoint(0, 20)];
    await historyPage(tester, report: report);
    expect(find.text('1 plotted of 1 returned buckets'), findsOneWidget);
    expect(find.text('2026-10-26T06:00:00.000Z'), findsWidgets);
  });

  testWidgets(
      'invalid and duplicate timestamps are disclosed without invented times',
      (tester) async {
    final report = historyFixture();
    (report['series'] as List)[0]
        ['points'] = [historyPoint(0, 20)..['at'] = '2026-02-31T06:00:00Z'];
    await historyPage(tester, report: report);
    expect(find.text('No valid bucket timestamps'), findsOneWidget);
    expect(find.text('Invalid buckets'), findsOneWidget);
    (report['series'] as List)[0]
        ['points'] = [historyPoint(0, 20), historyPoint(0, 30)];
    await historyPage(tester, report: report);
    expect(find.text('Series unavailable'), findsOneWidget);
    expect(find.byKey(const Key('history-plot')), findsNothing);
  });

  testWidgets(
      'host and range replacements reset selection and never retain previous readings',
      (tester) async {
    await historyPage(tester, report: historyFixture());
    await chooseMemory(tester);
    final changedRange = historyFixture()..['from'] = '2026-10-26T06:01:00Z';
    await historyPage(tester, report: changedRange);
    expect(find.text('CPU utilization · %'), findsOneWidget);
    await chooseMemory(tester);
    await historyPage(tester,
        host: monitoringHostFixture(id: 'host_b'),
        report: historyFixture(host: 'host_b'));
    expect(find.text('CPU utilization · %'), findsOneWidget);
    await historyPage(tester, host: monitoringHostFixture(id: 'host_c'));
    expect(find.text('History unavailable'), findsOneWidget);
    expect(find.byKey(const Key('history-plot')), findsNothing);
    expect(find.text('80.0 %'), findsNothing);
  });

  testWidgets(
      'retained offline/error data stays labelled and busy refresh is disabled',
      (tester) async {
    var refreshes = 0;
    await historyPage(tester,
        report: historyFixture(),
        host: monitoringHostFixture(connection: 'offline'),
        error: 'History service unavailable',
        onRefresh: () => refreshes++);
    expect(find.text('Retained history'), findsOneWidget);
    expect(find.text('History needs attention'), findsOneWidget);
    await tester
        .ensureVisible(find.widgetWithText(OutlinedButton, 'Refresh history'));
    await tester.tap(find.widgetWithText(OutlinedButton, 'Refresh history'));
    expect(refreshes, 1);
    await historyPage(tester,
        report: historyFixture(), loading: true, onRefresh: () => refreshes++);
    final button = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Refresh history'));
    expect(button.onPressed, isNull);
  });

  testWidgets('response limits and invalid range/offsets fail visibly',
      (tester) async {
    for (final patch in [
      {'from': '2026-10-26T06:00:00+99:99'},
      {'to': '2026-10-26T05:00:00Z'},
      {'step_seconds': 0},
      {
        'series': List.generate(17, (i) => {'name': 'series_$i', 'points': []})
      },
    ]) {
      await historyPage(tester, report: historyFixture()..addAll(patch));
      expect(find.text('History report unavailable'), findsOneWidget);
    }
    final report = historyFixture();
    (report['series'] as List)[0]['points'] =
        List.generate(1001, (_) => historyPoint(0, 20));
    await historyPage(tester, report: report);
    expect(find.text('Series unavailable'), findsOneWidget);
    expect(find.byKey(const Key('history-plot')), findsNothing);
  });

  for (final brightness in Brightness.values) {
    testWidgets('narrow large-text history remains usable in $brightness',
        (tester) async {
      await historyPage(tester,
          report: historyFixture(),
          size: const Size(360, 640),
          scale: 2,
          brightness: brightness);
      await tester.ensureVisible(
          find.widgetWithText(OutlinedButton, 'Previous bucket'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(OutlinedButton, 'Previous bucket'));
      await tester.pumpAndSettle();
      expect(find.text('Bucket 2 of 3'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'painted chart leaves missing and partially covered intervals unconnected',
      (tester) async {
    Future<int> inkInColumn(Json report, int column) async {
      await historyPage(tester, report: report);
      final paint =
          tester.widget<CustomPaint>(find.byKey(const Key('history-plot')));
      final primary =
          Theme.of(tester.element(find.byKey(const Key('history-plot'))))
              .colorScheme
              .primary;
      return (await tester.runAsync(() async {
        final recorder = ui.PictureRecorder();
        paint.painter!.paint(Canvas(recorder), const Size(240, 120));
        final picture = recorder.endRecording();
        final image = await picture.toImage(240, 120);
        final bytes =
            (await image.toByteData(format: ui.ImageByteFormat.rawRgba))!
                .buffer
                .asUint8List();
        var count = 0;
        for (var y = 0; y < 120; y++) {
          for (var x = column - 2; x <= column + 2; x++) {
            final i = (y * 240 + x) * 4;
            if (bytes[i] == (primary.r * 255).round() &&
                bytes[i + 1] == (primary.g * 255).round() &&
                bytes[i + 2] == (primary.b * 255).round() &&
                bytes[i + 3] > 200) {
              count++;
            }
          }
        }
        image.dispose();
        picture.dispose();
        return count;
      }))!;
    }

    final report = historyFixture();
    expect(await inkInColumn(report, 36),
        greaterThan(0)); // Adjacent full buckets.
    expect(await inkInColumn(report, 120), 0); // Missing minute 2 stays a gap.
    (report['series'] as List)[0]['points'][1]['coverage'] = .5;
    expect(
        await inkInColumn(report, 36), 0); // Partial coverage is not bridged.
  });
}
