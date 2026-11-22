import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/controllers/device_monitoring_controller.dart';
import 'package:odessy_desktop/features/devices/presentation/devices_hub.dart';
import 'package:odessy_desktop/features/monitoring/controllers/monitor_controller.dart';
import 'device_monitoring_controller_test.dart' show FakeMonitoring;
import 'saved_chat_navigation_test.dart'
    show capture, captureKey, loadCaptureFonts;

void main() {
  for (final narrow in [false, true]) {
    testWidgets(
        'SSH connected device overview/details ${narrow ? 'narrow' : 'wide'}',
        (t) async {
      t.view.physicalSize =
          narrow ? const Size(480, 1000) : const Size(1200, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      await loadCaptureFonts();
      final api =
          LocalApi(Uri.parse('http://127.0.0.1:1'), 'unused-fixture-token');
      final session = DesktopSession(api);
      final monitor = MonitorController();
      final fake = FakeMonitoring();
      final now = DateTime.now().toUtc().toIso8601String();
      final sample = <String, dynamic>{
        'observed_at': now,
        'cpu': {'status': 'ok', 'load_1': .08, 'load_5': .05, 'load_15': .07},
        'memory': {'status': 'unsupported'},
        'disks': {
          'status': 'ok',
          'disks': [
            {
              'id': '/',
              'total_bytes': 249366470656,
              'used_bytes': 76724490240,
              'available_bytes': 160463564800,
              'utilization_percent': 33
            }
          ]
        },
        'gpu': {'status': 'unsupported', 'devices': []},
      };
      fake.hosts = [
        MonitoringHost.fromJson({
          'host_id': 'host_ssh_fixture',
          'display_name': 'ubuntu-jetson',
          'trust': {'state': 'unknown'},
          'connectivity': {'state': 'online'},
          'monitoring_access': 'allowed',
          'connection': {'method': 'ssh', 'alias': 'jaimef@10.1.1.2'},
          'sample': sample,
        })
      ];
      fake.metricReply = (id) async => {
            'schema_version': 1,
            'host_id': id,
            'sample': sample,
            'stale_groups': [],
            'services': []
          };
      final devices = DeviceMonitoringController(createClient: (_) => fake);
      await t.pumpWidget(RepaintBoundary(
          key: captureKey,
          child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: odessyTheme(narrow ? Brightness.dark : Brightness.light),
              home: Scaffold(
                  body: Column(children: [
                const MaterialBanner(
                    content: Text(
                        'SOURCE PREVIEW · Simulated SSH computer · No live Jetson connection'),
                    actions: [SizedBox.shrink()]),
                Expanded(
                    child: DevicesHub(
                        session: session,
                        monitoring: monitor,
                        devices: devices,
                        onConnect: () {})),
              ])))));
      await t.pumpAndSettle();
      expect(find.text('ubuntu-jetson'), findsOneWidget);
      expect(find.text('jaimef@10.1.1.2'), findsOneWidget);
      expect(find.text('SSH · read-only monitoring'), findsOneWidget);
      expect(find.text('Connection: online'), findsOneWidget);
      expect(
          find.textContaining('no verified connection method'), findsNothing);
      await capture(
          t, narrow ? '10-devices-overview-narrow' : '08-devices-overview');
      await t.tap(find.text('View details'));
      await t.pumpAndSettle();
      expect(find.text('ubuntu-jetson'), findsWidgets);
      expect(find.text('SSH · read-only monitoring'), findsWidgets);
      expect(t.takeException(), isNull);
      await capture(
          t, narrow ? '11-device-details-narrow' : '09-device-details');
      await t.pumpWidget(const SizedBox.shrink());
      devices.dispose();
      monitor.dispose();
      session.dispose();
      api.close();
      await t.pump();
    });
  }
}
