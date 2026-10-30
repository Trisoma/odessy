import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/features/conversation/presentation/conversation.dart';
import 'package:odessy_desktop/features/conversation/presentation/interaction_card.dart';
import 'package:odessy_desktop/features/devices/presentation/devices.dart';
import 'package:odessy_desktop/features/devices/presentation/monitoring_details.dart';
import 'package:odessy_desktop/features/settings/presentation/settings.dart';
import 'package:odessy_desktop/features/shell/presentation/device_sidebar.dart';
import 'conversation_presentation_test.dart';
import 'monitoring_presentation_test.dart';
import 'settings_presentation_test.dart';

final _paintColorResults = <Json>[];

void verifyPaintColors(WidgetTester tester, Finder finder, String name) {
  final element = tester.element(finder);
  final widget = element.widget;
  final TextStyle style;
  if (widget is Text) {
    style = DefaultTextStyle.of(element).style.merge(widget.style);
  } else if (widget is EditableText) {
    style = widget.style;
  } else {
    throw StateError('Unsupported text probe');
  }
  final foreground = style.foreground?.color ?? style.color;
  Color? background;
  element.visitAncestorElements((ancestor) {
    if (ancestor.widget is Material &&
        (ancestor.widget as Material).color != null) {
      background = (ancestor.widget as Material).color;
      return false;
    }
    return true;
  });
  expect(foreground, isNotNull, reason: name);
  expect(background, isNotNull, reason: name);
  expect(background!.a, 1, reason: 'Probe requires an opaque Material surface');
  final a = Color.alphaBlend(foreground!, background!).computeLuminance();
  final b = background!.computeLuminance();
  final ratio = ((a > b ? a : b) + .05) / ((a > b ? b : a) + .05);
  _paintColorResults.add({
    'probe': name,
    'foreground_argb': foreground.toARGB32(),
    'background_argb': background!.toARGB32(),
    'ratio': ratio
  });
  expect(ratio, greaterThanOrEqualTo(4.5), reason: name);
}

Future<void> mountAccessible(
    WidgetTester tester, Widget body, Brightness brightness,
    {bool reduceMotion = false,
    bool highContrast = false,
    Size size = const Size(1200, 1100)}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: odessyTheme(brightness),
      home: MediaQuery(
          data: MediaQueryData(
              size: size,
              disableAnimations: reduceMotion,
              highContrast: highContrast),
          child: Scaffold(body: body))));
  await tester.pumpAndSettle();
}

void main() {
  tearDownAll(() {
    Directory('build/review').createSync(recursive: true);
    File('build/review/paint-color-contrast.json').writeAsStringSync(
        const JsonEncoder.withIndent('  ').convert(_paintColorResults));
  });
  for (final brightness in Brightness.values) {
    testWidgets('resolved text paint colors retain contrast in $brightness',
        (tester) async {
      final api = ConversationFixtureApi()..pending = [interactionFixture()];
      final c = DesktopSession(api);
      addTearDown(c.dispose);
      await c.start();
      await mountAccessible(
          tester,
          SingleChildScrollView(
              child: InteractionCard(
                  entry: c.session!.pending.single, controller: c)),
          brightness);
      verifyPaintColors(tester, find.text('Approve once'),
          '${brightness.name}: approval action');
      verifyPaintColors(
          tester, find.text('Tool'), '${brightness.name}: request label');
      await mountAccessible(
          tester,
          MonitoringDetails(
              host: monitoringHostFixture(), report: monitoringReportFixture()),
          brightness);
      verifyPaintColors(
          tester,
          find.byWidgetPredicate((widget) =>
              widget is EditableText && widget.controller.text == 'Allowed'),
          '${brightness.name}: selectable state');
    });
  }

  testWidgets('screen and request titles are screen-reader headings',
      (tester) async {
    final handle = tester.ensureSemantics();
    final api = ConversationFixtureApi()..pending = [interactionFixture()];
    final c = DesktopSession(api);
    await c.start();
    try {
      final scenes = <(Widget, String)>[
        (
          DevicePage(device: 'Accessible workstation', controller: c),
          'Accessible workstation'
        ),
        (ConversationPage(controller: c), 'Session'),
        (SettingsPage(controller: c), 'Global settings'),
        (
          SingleChildScrollView(
              child: InteractionCard(
                  entry: c.session!.pending.single, controller: c)),
          'Approval required'
        ),
        (
          MonitoringDetails(
              host: monitoringHostFixture(name: 'Reported workstation')),
          'Reported workstation'
        ),
        (
          SizedBox(
              width: 260,
              child: DeviceSidebar(
                  hosts: const [],
                  onInspect: (_) {},
                  onConversation: () {},
                  onSettings: () {})),
          'DEVICES'
        ),
      ];
      for (final scene in scenes) {
        await mountAccessible(tester, scene.$1, Brightness.dark);
        expect(
            tester.getSemantics(find.text(scene.$2)).flagsCollection.isHeader,
            true,
            reason: scene.$2);
      }
    } finally {
      handle.dispose();
      c.dispose();
    }
  });

  for (final brightness in Brightness.values) {
    testWidgets(
        'latest-message navigation respects reduced motion in $brightness',
        (tester) async {
      final api = ConversationFixtureApi()
        ..messages = List.generate(
            30,
            (index) => ChatMessage(
                'assistant', 'Update $index: ${'workspace detail ' * 10}'))
        ..activity = [
          SessionEvent(1, 'cost', {
            'total': {'calls': 1},
            'total_cost': null
          })
        ];
      final c = DesktopSession(api);
      addTearDown(c.dispose);
      await c.start();
      await mountAccessible(tester, ConversationPage(controller: c), brightness,
          reduceMotion: true, highContrast: true, size: const Size(1100, 750));
      final position = tester
          .state<ScrollableState>(find
              .descendant(
                  of: find.byKey(const Key('conversation-transcript')),
                  matching: find.byType(Scrollable))
              .first)
          .position;
      position.jumpTo(300);
      await tester.pump();
      await tester.tap(find.text('Latest messages'));
      for (var frame = 0; frame < 5; frame++) {
        await tester.pump();
      }
      expect(position.isScrollingNotifier.value, false);
      expect(position.extentAfter, lessThan(1));
    });
  }

  // Keep Flutter's deterministic test font for pixel contrast sampling.
  // Actual bundled-font rendering is inspected in the visual tests.
  for (final brightness in Brightness.values) {
    for (final scene in [
      'device',
      'messages',
      'approval',
      'settings',
      'sidebar',
      'monitoring'
    ]) {
      testWidgets(
          '$scene has labelled controls and readable contrast in $brightness',
          (tester) async {
        final semantics = tester.ensureSemantics();
        try {
          final api = ConversationFixtureApi()
            ..messages = [
              const ChatMessage('user', 'Inspect my workspace.'),
              const ChatMessage(
                  'assistant', 'I can help you review the changes.')
            ]
            ..pending = scene == 'approval' ? [interactionFixture()] : [];
          final controller =
              DesktopSession(scene == 'settings' ? SettingsFixtureApi() : api);
          addTearDown(controller.dispose);
          await controller.start();
          final Widget body = switch (scene) {
            'device' =>
              DevicePage(device: 'Demo workstation', controller: controller),
            'messages' => ConversationPage(controller: controller),
            'approval' => SingleChildScrollView(
                child: InteractionCard(
                    entry: controller.session!.pending.single,
                    controller: controller)),
            'settings' => SettingsPage(controller: controller),
            'sidebar' => SizedBox(
                width: 260,
                child: DeviceSidebar(
                    hosts: [monitoringHostFixture()],
                    selectedHostId: 'host_a',
                    demo: true,
                    onInspect: (_) {},
                    onConversation: () {},
                    onSettings: () {})),
            _ => MonitoringDetails(
                host: monitoringHostFixture(),
                report: monitoringReportFixture(),
                demo: true,
                onRefresh: () {}),
          };
          await mountAccessible(tester, body, brightness);
          if (scene == 'settings') {
            await selectSettingsSection(tester, SettingsSections.models);
          }
          await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
          await expectLater(tester, meetsGuideline(textContrastGuideline));
          expect(tester.takeException(), isNull);
        } finally {
          semantics.dispose();
        }
      });
    }
  }
}
