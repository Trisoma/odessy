import 'dart:ui' as ui;
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/conversation/presentation/interaction_card.dart';
import 'package:odessy_desktop/features/monitoring/presentation/metric_presentation.dart';

Map<String, dynamic> _approval() => {
      'id': 'approval-1',
      'kind': 'approval',
      'authoritative_prompt': 'Run ls in the workspace on host_edge.',
      'tool_name': 'run_command',
      'execution_target': 'host_edge',
      'permission_scopes': ['workspace:read', 'process:exec'],
      'risk': 'high',
      'arguments': {'command': 'ls -la /workspace', 'cwd': '/workspace'},
      'expires_at':
          DateTime.now().toUtc().add(const Duration(minutes: 5)).millisecondsSinceEpoch /
              1000,
    };

void main() {
  setUpAll(() async {
    await (FontLoader('OdessySans')
          ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
          ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
        .load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
  });

  testWidgets('approval card shows review facts, keeps the exact command and '
      'hides the raw payload behind Details', (tester) async {
    tester.view.physicalSize = const Size(760, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final session = DesktopSession(DemoApi());
    addTearDown(session.dispose);
    final key = GlobalKey();

    await tester.pumpWidget(MaterialApp(theme: ThemeData(fontFamily: 'OdessySans'),
        home: Scaffold(
            body: SingleChildScrollView(
                child: RepaintBoundary(
                    key: key,
                    child: InteractionCard(
                        entry: _approval(), controller: session))))));
    await tester.pump(const Duration(milliseconds: 50));

    // Human-readable facts lead the card.
    expect(find.text('Action'), findsOneWidget);
    expect(find.text('run_command'), findsOneWidget);
    expect(find.text('Execution target'), findsOneWidget);
    expect(find.text('host_edge'), findsOneWidget);
    expect(find.text('Permission scope'), findsOneWidget);
    expect(find.text('workspace:read, process:exec'), findsOneWidget);
    expect(find.text('Risk'), findsOneWidget);
    expect(find.text('high'), findsOneWidget);

    // The exact authorized command stays visible, not summarized away.
    expect(find.textContaining('ls -la /workspace'), findsWidgets);

    // Raw JSON is collapsed until asked for.
    expect(find.text('Details'), findsOneWidget);
    expect(find.textContaining('"tool_name"'), findsNothing);

    await tester.tap(find.text('Details'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.textContaining('"tool_name": "run_command"'), findsOneWidget);

    await tester.runAsync(() async {
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final image = await boundary.toImage(pixelRatio: 1);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/preview/ux-completion')
          .create(recursive: true);
      await File('build/preview/ux-completion/approval-review-expanded.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
  });

  testWidgets('metric card renders units, status and a diagnostic payload',
      (tester) async {
    final summary = summariseGroup('memory', {
      'status': 'ok',
      'observed_at': '2026-01-01T00:00:10Z',
      'total_bytes': 8589934592,
      'used_bytes': 4294967296,
      'utilization_percent': 50,
    });
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: MetricCard(summary: summary))));
    await tester.pump(const Duration(milliseconds: 50));

    expect(find.text('Memory'), findsOneWidget);
    expect(find.text('Current'), findsOneWidget);
    expect(find.text('8.0 GiB'), findsOneWidget);
    expect(find.text('4.0 GiB'), findsOneWidget);
    expect(find.text('50.0%'), findsOneWidget);
    expect(find.textContaining('"total_bytes"'), findsNothing);

    await tester.tap(find.text('Show payload'));
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.textContaining('"total_bytes"'), findsOneWidget);
  });
}
