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
      await tester.pumpWidget(OdessyApp(api: DemoApi(delay: Duration.zero)));
      await tester.pumpAndSettle();
      final compact = size.width < 900;
      if (compact) {
        await tester.tap(find.byTooltip('Open navigation menu'));
        await tester.pumpAndSettle();
      }

      expect(tester.takeException(), isNull);
      final footer = find.widgetWithText(ListTile, 'Settings');
      expect(footer.hitTestable(), findsOneWidget);
      final footerRect = tester.getRect(footer);
      expect(footerRect.bottom, lessThanOrEqualTo(size.height));
      expect(tester.getRect(find.text('Demo · no tools execute')).bottom,
          lessThanOrEqualTo(size.height));

      final scrollable = find.descendant(
          of: find.byKey(const Key('device-sidebar-scroll')),
          matching: find.byType(Scrollable));
      await tester.scrollUntilVisible(find.text('Tools & installation'), 160,
          scrollable: scrollable);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Tools & installation'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Runtime maintenance'), 160,
          scrollable: scrollable);
      await tester.pumpAndSettle();
      expect(find.text('Runtime maintenance').hitTestable(), findsOneWidget);
      expect(tester.getRect(footer), footerRect);
      await tester.scrollUntilVisible(find.text('Jetson'), -160,
          scrollable: scrollable);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Jetson'));
      await tester.pumpAndSettle();
      expect(
          find.text('Device details and available settings.'), findsOneWidget);
      if (compact) {
        await tester.tap(find.byTooltip('Open navigation menu'));
        await tester.pumpAndSettle();
      }
      expect(tester.getRect(footer), footerRect);

      await tester.tap(footer);
      await tester.pumpAndSettle();
      if (compact) {
        await tester.sendKeyEvent(LogicalKeyboardKey.escape);
        await tester.pumpAndSettle();
      }
      expect(find.text('Preferences').hitTestable(), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
    });
  }
}
