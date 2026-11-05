import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/conversation/presentation/activity_usage_page.dart';

void main() {
  for (final brightness in Brightness.values) {
    testWidgets('usage page handles reports and updates in $brightness',
        (tester) async {
      tester.view.physicalSize = const Size(600, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      Widget page(List<SessionEvent> events, {bool connected = true}) =>
          MaterialApp(
              theme: ThemeData(brightness: brightness),
              home: Scaffold(
                  body:
                      ActivityUsagePage(events: events, connected: connected)));
      await tester.pumpWidget(page([], connected: false));
      expect(find.textContaining('Service disconnected'), findsOneWidget);
      expect(find.text('No usage report is available in this session.'),
          findsOneWidget);
      final event = SessionEvent(1, 'cost', {
        'total': {'calls': 2, 'prompt': 42, 'cached': 3, 'completion': 8},
        'total_cost': 0.05,
        'currency': 'USD',
        'profiles': [
          {
            'participant': 'Assistant',
            'provider': 'Example',
            'model': 'model-a'
          }
        ],
      });
      await tester.pumpWidget(page([event]));
      await tester.pumpAndSettle();
      expect(find.text('42'), findsOneWidget);
      expect(find.text('0.05 USD'), findsOneWidget);
      await tester.ensureVisible(find.text('Assistant · Example · model-a'));
      expect(find.text('Assistant · Example · model-a'), findsOneWidget);
      await tester.ensureVisible(find.text('Activity & provenance'));
      await tester.tap(find.text('Activity & provenance'));
      await tester.pumpAndSettle();
      expect(find.text('Cost report'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
