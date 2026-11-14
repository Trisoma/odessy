import 'component_settings_test.dart' show SavedComponents;
import 'settings_presentation_test.dart' show selectSettingsSection;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/app.dart';

void main() {
  for (final (size, pixelRatio) in const [
    (Size(1200, 480), 1.0),
    (Size(720, 480), 1.0),
    (Size(1200, 360), 1.0),
    (Size(360, 480), 1.0),
    (Size(1200, 480), 1.25),
    (Size(720, 480), 1.25),
    (Size(720, 480), 1.5),
    (Size(1200, 480), 2.0),
    (Size(720, 480), 2.0),
  ]) {
    testWidgets('short shell keeps footer reachable at $size, DPR $pixelRatio',
        (tester) async {
      tester.view.physicalSize = size * pixelRatio;
      tester.view.devicePixelRatio = pixelRatio;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      if (size.width == 360) {
        // Match the native bundled font at the narrowest width. The placeholder
        // test font independently overflows the unchanged main-pane header.
        await (FontLoader('OdessySans')
              ..addFont(rootBundle.load('assets/fonts/roboto-regular.ttf'))
              ..addFont(rootBundle.load('assets/fonts/roboto-bold.ttf')))
            .load();
        await (FontLoader('MaterialIcons')
              ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
            .load();
      }
      await tester.pumpWidget(OdessyApp(
          api: DemoApi(delay: Duration.zero), setupBackend: SavedComponents()));
      await tester.pumpAndSettle();
      final compact = size.width < 900;
      if (compact) {
        await tester.tap(find.byTooltip('Open navigation menu'));
        await tester.pumpAndSettle();
      }

      expect(tester.takeException(), isNull);
      final footer = find.byKey(const ValueKey('nav-Settings')).hitTestable();
      expect(footer.hitTestable(), findsOneWidget);
      final footerRect = tester.getRect(footer);
      expect(footerRect.bottom, lessThanOrEqualTo(size.height));
      // Maintenance now lives in Settings; hosts are selected from Devices.
      // The pinned rail footer must remain reachable at every short viewport.
      await tester.tap(footer);
      await tester.pumpAndSettle();
      await selectSettingsSection(tester, 'Installation');
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Runtime maintenance'));
      expect(find.text('Runtime maintenance').hitTestable(), findsOneWidget);
      await tester.tap(find.text('Runtime maintenance'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('native-root')), findsOneWidget);
      await tester.tap(find.byTooltip('Back to Settings'));
      await tester.pumpAndSettle();
      if (compact) {
        await tester.tap(find.byTooltip('Open navigation menu'));
        await tester.pumpAndSettle();
      }
      expect(tester.getRect(footer), footerRect);
      await tester.tap(find.byKey(const ValueKey('nav-Devices')).hitTestable());
      await tester.pumpAndSettle();
      final details = find.descendant(
          of: find.byKey(const ValueKey('host-card-host_demo_jetson')),
          matching: find.widgetWithText(TextButton, 'View details'));
      if (details.evaluate().isEmpty) {
        await tester.scrollUntilVisible(details, 120,
            scrollable: find
                .descendant(
                    of: find.byKey(const Key('devices-overview-scroll')),
                    matching: find.byType(Scrollable))
                .first);
      }
      await tester.ensureVisible(details);
      await tester.pumpAndSettle();
      await tester.tap(details);
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('device-detail-scroll')), findsOneWidget);
      if (compact) {
        await tester.tap(find.byTooltip('Open navigation menu'));
        await tester.pumpAndSettle();
      }
      expect(tester.getRect(footer), footerRect);
      await tester.tap(footer);
      await tester.pumpAndSettle();
      expect(find.text('Preferences').hitTestable(), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
    });
  }
}
