import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_details.dart';
import 'package:odessy_desktop/features/shell/presentation/device_sidebar.dart';
import 'monitoring_presentation_test.dart';

/// Composition fixture only, not a replacement for the protected app shell.
class MonitoringComponentHarness extends StatefulWidget {
  const MonitoringComponentHarness({super.key});
  @override
  State<MonitoringComponentHarness> createState() =>
      _MonitoringComponentHarnessState();
}

class _MonitoringComponentHarnessState
    extends State<MonitoringComponentHarness> {
  Brightness brightness = Brightness.dark;
  String selected = 'host_a';
  final hosts = [
    monitoringHostFixture(trust: 'local'),
    monitoringHostFixture(
        id: 'host_b', name: 'Demo Jetson', connection: 'offline'),
    monitoringHostFixture(
        id: 'host_c',
        name: 'Demo laptop',
        connection: 'offline',
        trust: 'revoked',
        access: 'denied')
  ];
  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: odessyTheme(brightness),
        home: Scaffold(
            body: Column(children: [
          Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
              child: Row(children: [
                const Expanded(
                    child: Text(
                        'COMPONENT TEST · fixtures only · not connected to a live service')),
                IconButton(
                    tooltip: 'Toggle theme',
                    icon: const Icon(Icons.contrast),
                    onPressed: () => setState(() => brightness =
                        brightness == Brightness.dark
                            ? Brightness.light
                            : Brightness.dark)),
              ])),
          const Divider(height: 1),
          Expanded(
              child: Row(children: [
            SizedBox(
                width: 256,
                child: DeviceSidebar(
                  hosts: hosts,
                  demo: true,
                  selectedHostId: selected,
                  onInspect: (id) => setState(() => selected = id),
                  onConversation: () {},
                  onSettings: () {},
                  connectionLabel:
                      'Development fixtures · no devices contacted',
                  destinations: const [
                    SidebarDestination(
                        label: 'Runtime maintenance',
                        icon: Icons.build_outlined,
                        onTap: null)
                  ],
                )),
            const VerticalDivider(width: 1),
            Expanded(
                child: MonitoringDetails(
                    host: hosts.firstWhere((host) => host.id == selected),
                    report: monitoringReportFixture(id: selected),
                    demo: true)),
          ])),
        ])),
      );
}

void main() {
  testWidgets('render monitoring component screenshots', (tester) async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
    tester.view.physicalSize = const Size(1440, 1150);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final key = GlobalKey();
    Future<void> capture(String name) async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final frame = await boundary.toImage(pixelRatio: 1);
      final bytes = await frame.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/screenshots/monitoring').create(recursive: true);
      await File('build/screenshots/monitoring/$name.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      frame.dispose();
    }

    await tester.pumpWidget(
        RepaintBoundary(key: key, child: const MonitoringComponentHarness()));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('monitoring-dark'));
    await tester.tap(find.byTooltip('Toggle theme'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('monitoring-light'));
    await tester.tap(find.byKey(const ValueKey('inspect-host_b')));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('offline-light'));
    await tester.tap(find.byKey(const ValueKey('inspect-host_c')));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('revoked-light'));

    Future<void> scene(Widget child, Size size) async {
      tester.view.physicalSize = size;
      await tester.pumpWidget(RepaintBoundary(
          key: key,
          child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: odessyTheme(Brightness.dark),
              home: Scaffold(body: child))));
      await tester.pump();
    }

    await scene(
        MonitoringDetails(
            host: monitoringHostFixture(),
            report: monitoringReportFixture(),
            demo: true),
        const Size(390, 1100));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('monitoring-narrow'));
    await scene(const MonitoringDetails(loading: true), const Size(900, 600));
    await tester.runAsync(() => capture('loading-dark'));
    await scene(const MonitoringDetails(error: 'Monitoring is unavailable.'),
        const Size(900, 600));
    await tester.pumpAndSettle();
    await tester.runAsync(() => capture('unavailable-dark'));
    expect(tester.takeException(), isNull);
  });
}
