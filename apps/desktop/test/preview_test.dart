import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/app.dart';
import 'package:odessy_client/odessy_client.dart';

void main() {
  testWidgets('preview sends, stops and opens device settings', (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(OdessyApp(api: DemoApi()));
    await tester.pumpAndSettle();
    expect(find.text('DEMO'), findsWidgets);
    await tester.enterText(find.byKey(const Key('composer')), 'Hello Odessy');
    await tester.tap(find.byTooltip('Send message'));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Hello Odessy'), findsOneWidget);
    await tester.tap(find.byTooltip('Stop current turn'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Stopped'), findsWidgets);
    await tester.tap(find.text('Jetson'));
    await tester.pumpAndSettle();
    expect(find.textContaining('does not change'), findsOneWidget);
    expect(find.text('Pairing management API is not available yet.'),
        findsOneWidget);
  });
  testWidgets('approval is explicit and distinct from a question',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
        OdessyApp(api: DemoApi(delay: const Duration(milliseconds: 20))));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('composer')), '/approval');
    await tester.tap(find.byTooltip('Send message'));
    await tester.pumpAndSettle();
    expect(find.text('Approve once'), findsOneWidget);
    expect(find.text('Deny'), findsOneWidget);
    final deny = find.widgetWithText(OutlinedButton, 'Deny');
    await tester.ensureVisible(deny);
    await tester.pumpAndSettle();
    await tester.tap(deny);
    await tester.pumpAndSettle();
    expect(find.text('Approve once'), findsNothing);
  });
}
