import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_details.dart';
import 'package:odessy_desktop/features/shell/presentation/device_sidebar.dart';

MonitoringHost monitoringHostFixture(
        {String id = 'host_a',
        String name = 'Demo workstation',
        String trust = 'paired',
        String connection = 'online',
        String access = 'allowed',
        Json? sample}) =>
    MonitoringHost.fromJson({
      'host_id': id,
      'display_name': name,
      'trust': {'state': trust},
      'connectivity': {'state': connection},
      'monitoring_access': access,
      'sample': sample
    });

Json monitoringReportFixture({String id = 'host_a'}) => {
      'schema_version': 1,
      'host_id': id,
      'stale_groups': <String>[],
      'sample': {
        'observed_at': '2026-10-25T06:00:00Z',
        'cpu': {'status': 'ok', 'utilization_percent': 17.5},
        'memory': {
          'status': 'ok',
          'used_bytes': 8589934592,
          'total_bytes': 17179869184
        },
        'gpu': {'status': 'unsupported', 'devices': []}
      },
      'services': [
        {
          'name': 'coordinator',
          'status': 'healthy',
          'observed_at': '2026-10-25T06:00:00Z'
        }
      ],
    };

Future<void> mountMonitoring(WidgetTester tester, Widget child,
    {Size size = const Size(1100, 1100),
    double scale = 1,
    Brightness brightness = Brightness.dark,
    bool settle = true}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: odessyTheme(brightness),
      home: MediaQuery(
          data:
              MediaQueryData(size: size, textScaler: TextScaler.linear(scale)),
          child: Scaffold(body: child))));
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
  }
}

void main() {
  testWidgets('detail stays at the top of a loose-height Row for short reports',
      (tester) async {
    await mountMonitoring(
        tester,
        Row(children: [
          Expanded(
              child: MonitoringDetails(
            host: monitoringHostFixture(
                name: 'Short report', trust: 'revoked', access: 'denied'),
          ))
        ]));
    expect(tester.getTopLeft(find.text('Short report')).dy, closeTo(24, 1));
  });
  testWidgets('sidebar distinguishes hosts with identical names by stable IDs',
      (tester) async {
    final picked = <String>[];
    var conversation = 0, settings = 0;
    await mountMonitoring(
        tester,
        SizedBox(
            width: 260,
            child: DeviceSidebar(
              hosts: [
                monitoringHostFixture(name: 'Same name'),
                monitoringHostFixture(
                    id: 'host_b',
                    name: 'Same name',
                    connection: 'offline',
                    trust: 'revoked')
              ],
              selectedHostId: 'host_b',
              onInspect: picked.add,
              onConversation: () => conversation++,
              onSettings: () => settings++,
            )));
    await tester.tap(find.byKey(const ValueKey('inspect-host_b')));
    expect(picked, ['host_b']);
    expect(
        tester
            .widget<ListTile>(find.byKey(const ValueKey('inspect-host_b')))
            .selected,
        true);
    expect(find.text('Offline · Revoked'), findsOneWidget);
    await tester.tap(find.text('Conversation'));
    await tester.tap(find.text('Settings'));
    expect([conversation, settings], [1, 1]);
  });

  testWidgets('sidebar shows empty failure and retained states honestly',
      (tester) async {
    await mountMonitoring(
        tester,
        SizedBox(
            width: 260,
            child: DeviceSidebar(
              hosts: const [],
              onInspect: (_) {},
              onConversation: () {},
              onSettings: () {},
            )));
    expect(find.text('No devices reported'), findsOneWidget);
    expect(find.text('This computer'), findsNothing);
    await mountMonitoring(
        tester,
        SizedBox(
            width: 260,
            child: DeviceSidebar(
              hosts: [monitoringHostFixture()],
              retained: true,
              error: 'Monitoring service unavailable',
              onInspect: (_) {},
              onConversation: () {},
              onSettings: () {},
            )));
    expect(find.text('Last known device list'), findsOneWidget);
    expect(find.textContaining('last report'), findsOneWidget);
    expect(find.text('Demo devices · fixtures only'), findsNothing);
  });

  testWidgets('background device refresh keeps the list in place',
      (tester) async {
    Widget sidebar(bool loading) => SizedBox(
        width: 260,
        child: DeviceSidebar(
          hosts: [monitoringHostFixture()],
          loading: loading,
          onInspect: (_) {},
          onConversation: () {},
          onSettings: () {},
          onRefresh: () {},
        ));
    await mountMonitoring(tester, sidebar(false));
    final position =
        tester.getTopLeft(find.byKey(const ValueKey('inspect-host_a')));
    await mountMonitoring(tester, sidebar(true));
    expect(tester.getTopLeft(find.byKey(const ValueKey('inspect-host_a'))),
        position);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    expect(find.text('Last known device list'), findsNothing);
    expect(
        tester
            .widget<IconButton>(find.widgetWithIcon(IconButton, Icons.refresh))
            .onPressed,
        isNotNull);
  });

  testWidgets('initial device load remains visible', (tester) async {
    await mountMonitoring(
        tester,
        DeviceSidebar(
          hosts: const [],
          loading: true,
          onInspect: (_) {},
          onConversation: () {},
          onSettings: () {},
        ),
        settle: false);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
  });

  testWidgets('background metrics refresh keeps the report stable',
      (tester) async {
    await mountMonitoring(
        tester,
        MonitoringDetails(
            host: monitoringHostFixture(),
            report: monitoringReportFixture(),
            loading: true,
            onRefresh: () {}));
    expect(find.byType(LinearProgressIndicator), findsNothing);
    expect(find.text('17.5%'), findsOneWidget);
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, 'Refresh monitoring'))
            .onPressed,
        isNotNull);
  });

  testWidgets(
      'sidebar keeps settings accessible with many devices and expanded tools',
      (tester) async {
    var settings = 0, tools = 0;
    final sidebar = DeviceSidebar(
        hosts: List.generate(25, (i) => monitoringHostFixture(id: 'host_$i')),
        onInspect: (_) {},
        onConversation: () {},
        onSettings: () => settings++,
        destinations: [
          SidebarDestination(
              label: 'Runtime maintenance',
              icon: Icons.build_outlined,
              onTap: () => tools++)
        ]);
    await mountMonitoring(tester, SizedBox(width: 260, child: sidebar),
        size: const Size(500, 500));
    await tester.tap(find.text('Settings'));
    expect(settings, 1);
    await tester.scrollUntilVisible(find.text('Tools & installation'), 400,
        scrollable: find.descendant(
            of: find.byKey(const Key('device-sidebar-scroll')),
            matching: find.byType(Scrollable)));
    await tester.tap(find.text('Tools & installation'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Runtime maintenance'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Runtime maintenance'));
    await tester.tap(find.text('Settings'));
    expect([tools, settings], [1, 2]);
  });

  testWidgets('sidebar keyboard activation inspects without implicit routing',
      (tester) async {
    final picked = <String>[];
    await mountMonitoring(
        tester,
        SizedBox(
            width: 260,
            child: DeviceSidebar(
              hosts: [monitoringHostFixture()],
              onInspect: picked.add,
              onConversation: () {},
              onSettings: () {},
            )));
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(picked, ['host_a']);
  });

  testWidgets(
      'monitoring keeps trust connectivity access and quality independent',
      (tester) async {
    await mountMonitoring(
        tester,
        MonitoringDetails(
            host: monitoringHostFixture(),
            report: monitoringReportFixture(),
            demo: true));
    expect(find.text('Online'), findsOneWidget);
    expect(find.text('Paired'), findsOneWidget);
    expect(find.text('Allowed'), findsOneWidget);
    expect(find.text('17.5%'), findsOneWidget);
    expect(find.text('8.00 GiB'), findsOneWidget);
    expect(find.text('16.00 GiB'), findsOneWidget);
    expect(find.text('Collector: Unsupported'), findsOneWidget);
    expect(find.text('2026-10-25T06:00:00.000Z'), findsWidgets);
    expect(find.text('Healthy'), findsOneWidget);
  });

  testWidgets('stale group and retained offline reports cannot look current',
      (tester) async {
    final report = monitoringReportFixture()..['stale_groups'] = ['cpu'];
    await mountMonitoring(tester,
        MonitoringDetails(host: monitoringHostFixture(), report: report));
    expect(find.text('Historical / stale reading'), findsOneWidget);
    await mountMonitoring(
        tester,
        MonitoringDetails(
            host: monitoringHostFixture(connection: 'offline'),
            report: report,
            retained: true,
            error: 'Connection lost'));
    expect(find.text('Historical report'), findsOneWidget);
    expect(find.text('Offline · last report'), findsOneWidget);
    expect(find.text('Paired · last report'), findsOneWidget);
    expect(find.text('17.5%'), findsOneWidget);
    expect(find.text('Healthy · last report'), findsOneWidget);
  });

  for (final state in ['denied', 'disabled', 'unsupported', 'revoked']) {
    testWidgets('$state suppresses supplied device telemetry', (tester) async {
      await mountMonitoring(
          tester,
          MonitoringDetails(
              host: monitoringHostFixture(
                  access: state == 'revoked' ? 'allowed' : state,
                  trust: state == 'revoked' ? 'revoked' : 'paired'),
              report: monitoringReportFixture()));
      expect(find.text('Telemetry unavailable'), findsOneWidget);
      expect(find.text('17.5%'), findsNothing);
      expect(find.text('Healthy'), findsNothing);
    });
  }

  for (final invalid in ['host', 'schema']) {
    testWidgets('rejects mismatched $invalid report', (tester) async {
      final report = monitoringReportFixture();
      if (invalid == 'host') {
        report['host_id'] = 'host_other';
      } else {
        report['schema_version'] = 2;
      }
      await mountMonitoring(tester,
          MonitoringDetails(host: monitoringHostFixture(), report: report));
      expect(find.text('Report unavailable'), findsOneWidget);
      expect(find.text('17.5%'), findsNothing);
      expect(find.text('Healthy'), findsNothing);
    });
  }

  testWidgets('missing invalid and warming readings do not turn into zero',
      (tester) async {
    final report = monitoringReportFixture();
    final sample = report['sample'] as Map;
    sample['cpu'] = {'status': 'warming_up', 'utilization_percent': 99};
    sample['memory'] = {
      'status': 'ok',
      'used_bytes': -1,
      'total_bytes': double.nan
    };
    sample['observed_at'] = 'not-a-time';
    await mountMonitoring(tester,
        MonitoringDetails(host: monitoringHostFixture(), report: report));
    expect(find.text('Collector: Warming up'), findsOneWidget);
    expect(find.text('Unavailable'), findsNWidgets(3));
    expect(find.text('99.0%'), findsNothing);
    expect(find.text('0.0%'), findsNothing);
    expect(find.text('0.00 GiB'), findsNothing);
    expect(find.text('Observation time unavailable'), findsWidgets);
  });

  testWidgets('real zero readings remain distinguishable from absent readings',
      (tester) async {
    final report = monitoringReportFixture();
    (report['sample'] as Map)['cpu'] = {
      'status': 'ok',
      'utilization_percent': 0
    };
    await mountMonitoring(tester,
        MonitoringDetails(host: monitoringHostFixture(), report: report));
    expect(find.text('0.0%'), findsOneWidget);
  });

  testWidgets('unsupported GPU suppresses even populated device measurements',
      (tester) async {
    final report = monitoringReportFixture();
    (report['sample'] as Map)['gpu'] = {
      'status': 'unsupported',
      'devices': [
        {'id': 'gpu_0', 'utilization_percent': 78.9}
      ]
    };
    await mountMonitoring(tester,
        MonitoringDetails(host: monitoringHostFixture(), report: report));
    expect(find.text('78.9%'), findsNothing);
    expect(find.text('GPU readings unavailable'), findsOneWidget);
  });

  testWidgets(
      'loading disables refresh and selection-free view invents no host',
      (tester) async {
    var refreshed = 0;
    await mountMonitoring(
        tester, MonitoringDetails(loading: true, onRefresh: () => refreshed++),
        settle: false);
    expect(find.text('Loading devices'), findsOneWidget);
    expect(
        tester
            .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, 'Refresh monitoring'))
            .onPressed,
        isNull);
    expect(find.text('Online'), findsNothing);
    await mountMonitoring(
        tester,
        MonitoringDetails(
            host: monitoringHostFixture(), onRefresh: () => refreshed++));
    final refresh = find.text('Refresh monitoring');
    await tester.ensureVisible(refresh);
    await tester.tap(refresh);
    expect(refreshed, 1);
  });

  for (final brightness in Brightness.values) {
    testWidgets('large text monitoring and compact sidebar fit $brightness',
        (tester) async {
      await mountMonitoring(
          tester,
          MonitoringDetails(
              host: monitoringHostFixture(
                  name: 'A workstation with a long display name'),
              report: monitoringReportFixture()),
          size: const Size(360, 640),
          scale: 2,
          brightness: brightness);
      await tester.ensureVisible(find.text('Refresh monitoring'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await mountMonitoring(
          tester,
          SizedBox(
              width: 236,
              child: DeviceSidebar(
                  hosts: [monitoringHostFixture()],
                  onInspect: (_) {},
                  onConversation: () {},
                  onSettings: () {},
                  demo: true,
                  connectionLabel: 'Demo · no devices contacted')),
          size: const Size(360, 400),
          scale: 2,
          brightness: brightness);
      expect(tester.takeException(), isNull);
    });
  }
}
