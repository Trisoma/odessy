import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_details.dart';

class _RealHttp extends HttpOverrides {}

void main() {
  final enabled = Platform.environment['ODESSY_REAL_MONITOR_E2E'] == '1';
  testWidgets('desktop shows local host and six real history series',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await HttpOverrides.runZoned(() async {
      final api = LocalApi(
          Uri.parse(Platform.environment['ODESSY_TEST_ENDPOINT']!),
          Platform.environment['ODESSY_TEST_TOKEN']!);
      final monitor = api.createMonitoringClient();
      Future<void> ready(bool Function() condition) async {
        for (var i = 0; i < 300; i++) {
          await tester.runAsync(
              () => Future<void>.delayed(const Duration(milliseconds: 50)));
          await tester.pump(const Duration(milliseconds: 50));
          if (condition()) return;
        }
        final details = tester
            .widgetList<Text>(find.byType(Text))
            .map((w) => w.data ?? '')
            .where((s) => RegExp(
                    'device|telemetry|monitor|allowed|unavailable|loading|offline|invalid|HTTP|error|retry',
                    caseSensitive: false)
                .hasMatch(s))
            .take(20)
            .toList();
        final detail = find.byType(MonitoringDetails).evaluate().isEmpty
            ? null
            : tester.widget<MonitoringDetails>(find.byType(MonitoringDetails));
        fail('Real monitoring UI did not settle: $details; '
            'host=${detail?.host != null}, loading=${detail?.loading}, error=${detail?.error}');
      }

      try {
        final snapshot = await tester.runAsync(() => monitor.listHosts());
        final local =
            snapshot!.hosts.singleWhere((host) => host.trust == 'local');
        expect(local.access, 'allowed');
        final detail = await tester.runAsync(() => monitor.getHost(local.id));
        expect(detail!.id, local.id);
        await tester.pumpWidget(OdessyApp(api: api));
        await ready(() =>
            find.byKey(ValueKey('inspect-${local.id}')).evaluate().isNotEmpty);
        expect(find.text('DEMO'), findsNothing);
        await tester.tap(find.byKey(ValueKey('inspect-${local.id}')));
        await tester.pump();
        await ready(() =>
            find.text('Telemetry access').evaluate().isNotEmpty &&
            find.text('Allowed').evaluate().isNotEmpty);
        await ready(() => find.textContaining('GiB').evaluate().isNotEmpty);
        final metrics =
            await tester.runAsync(() => monitor.getMetrics(local.id));
        final memory = (metrics!['sample'] as Map)['memory'] as Map;
        final totalGiB = (memory['total_bytes'] as num) / (1024 * 1024 * 1024);
        expect(find.text('${totalGiB.toStringAsFixed(2)} GiB'), findsWidgets);
        await tester.tap(find.widgetWithText(OutlinedButton, 'History'));
        await tester.pump();
        await ready(() =>
            find.byKey(const Key('history-metric')).evaluate().isNotEmpty);
        final picker = tester.widget<DropdownButton<String>>(
            find.byKey(const Key('history-metric')));
        expect(
            picker.items!.map((item) => item.value),
            containsAll([
              'cpu.utilization_percent',
              'memory.used_bytes',
              'memory.total_bytes',
              'memory.utilization_percent',
              'gpu.utilization_percent',
              'gpu.memory_used_bytes'
            ]));
        await tester.tap(find.byKey(const Key('monitoring-history-window')));
        // Live monitoring can keep animating; advance only the menu animation.
        await tester.pump(const Duration(milliseconds: 500));
        await tester.tap(find.text('Last 7 days').last);
        await tester.pump();
        await ready(
            () => find.text('900 seconds per bucket').evaluate().isNotEmpty);
        expect(find.text('DEMO'), findsNothing);
        api.close();
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pump();
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 20)));
        await tester.pumpAndSettle();
      } finally {
        api.close();
      }
    }, createHttpClient: (context) => _RealHttp().createHttpClient(context));
  }, skip: !enabled);
}
