import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/conversation/presentation/session_usage.dart';

void main() {
  Future<void> showUsage(WidgetTester tester, List<SessionEvent> events) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(child: SessionUsage(events: events)))));
    await tester.tap(find.text('API usage and estimated cost'));
    await tester.pumpAndSettle();
  }

  testWidgets('missing report remains unknown', (tester) async {
    await showUsage(tester, []);
    expect(find.text('No usage report is available in this session.'),
        findsOneWidget);
    expect(find.textContaining('Estimated total: 0'), findsNothing);
  });

  testWidgets(
      'uses latest reported process ledger without inventing a currency',
      (tester) async {
    await showUsage(tester, [
      SessionEvent(1, 'cost', {
        'total': {'calls': 1, 'prompt': 4},
        'total_cost': 9
      }),
      SessionEvent(2, 'cost', {
        'total': {'calls': 2, 'prompt': 42, 'cached': 7, 'completion': 8},
        'total_cost': 0.125
      })
    ]);
    expect(find.text('Report event #2'), findsOneWidget);
    expect(find.text('Input tokens: 42'), findsOneWidget);
    expect(find.text('Estimated total: 0.125 (currency not reported)'),
        findsOneWidget);
    expect(find.textContaining('process-wide'), findsOneWidget);
  });

  testWidgets('invalid or unpriced data is not displayed as zero',
      (tester) async {
    await showUsage(tester, [
      SessionEvent(1, 'cost', {
        'total': {'calls': -1, 'prompt': '9', 'cached': null},
        'total_cost': null
      })
    ]);
    expect(find.text('API calls: Not reported'), findsOneWidget);
    expect(find.text('Input tokens: Not reported'), findsOneWidget);
    expect(find.text('Estimated total: Not reported'), findsOneWidget);
  });

  testWidgets('shows reported currency with process-wide estimates',
      (tester) async {
    await showUsage(tester, [
      SessionEvent(
          1, 'cost', {'total_cost': 0.5, 'turn_cost': 0.25, 'currency': 'CNY'})
    ]);
    expect(find.text('Estimated total: 0.5 CNY'), findsOneWidget);
    expect(find.text('Estimated turn: 0.25 CNY'), findsOneWidget);
  });

  testWidgets('shows turn and profile amounts without inventing prices',
      (tester) async {
    await showUsage(tester, [
      SessionEvent(3, 'cost', {
        'total': {'calls': 2, 'prompt': 40, 'cached': 5, 'completion': 8},
        'turn': {'calls': 1, 'prompt': 9, 'cached': 2, 'completion': 3},
        'turn_cost': 0.01,
        'profiles': [
          {
            'participant': 'writer',
            'provider': 'vendor',
            'model': 'model-a',
            'total': {'calls': 1, 'prompt': 9, 'cached': 2, 'completion': 3},
            'total_cost': 0.01,
            'pricing': {'currency': 'USD'}
          },
          {
            'participant': 'reviewer',
            'provider': 'vendor',
            'model': 'model-b',
            'total': {'calls': 1, 'prompt': 31, 'cached': 3, 'completion': 5},
            'total_cost': null,
            'pricing': null
          },
        ]
      })
    ]);
    expect(find.text('Turn input tokens: 9'), findsOneWidget);
    expect(find.text('Estimated turn: 0.01 (currency not reported)'),
        findsOneWidget);
    expect(find.text('Estimated profile total: 0.01 USD'), findsOneWidget);
    expect(find.text('Estimated profile total: Not reported'), findsOneWidget);
    expect(find.textContaining('concurrent work'), findsOneWidget);
  });
}
