import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/monitoring/controllers/monitor_controller.dart';
import 'package:odessy_desktop/features/monitoring/presentation/monitor_page.dart';

void main() {
  testWidgets(
      'monitor explains unavailable transport without fabricated metrics',
      (tester) async {
    final controller = MonitorController();
    await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: MonitorPage(controller: controller))));
    expect(find.textContaining('Connect to a live Odessy service'),
        findsOneWidget);
    expect(find.text('0%'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
}
