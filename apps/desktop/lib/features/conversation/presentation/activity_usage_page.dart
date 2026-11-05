import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import 'session_activity.dart';

/// Displays the latest service ledger without treating estimates as billing.
class ActivityUsagePage extends StatelessWidget {
  const ActivityUsagePage(
      {super.key, required this.events, required this.connected});

  final List<SessionEvent> events;
  final bool connected;

  static String _count(Object? value) =>
      value is int && value >= 0 ? '$value' : 'Not reported';

  static String _cost(Object? value, Object? currency) {
    if (value is! num || !value.isFinite || value < 0) return 'Not reported';
    final unit =
        currency is String && RegExp(r'^[A-Za-z]{1,16}$').hasMatch(currency)
            ? currency
            : '(currency not reported)';
    return '$value $unit';
  }

  static String _name(Object? value) =>
      value is String && value.trim().isNotEmpty ? value : 'Not reported';

  @override
  Widget build(BuildContext context) {
    final reports = events.where((event) => event.type == 'cost');
    final report = reports.isEmpty ? null : reports.last;
    final data = report?.data ?? const {};
    final total = data['total'] is Map ? data['total'] as Map : const {};
    final turn = data['turn'] is Map ? data['turn'] as Map : const {};
    final profiles =
        data['profiles'] is List ? data['profiles'] as List : const [];
    final theme = Theme.of(context);
    return SingleChildScrollView(
      key: const Key('activity-usage-page'),
      padding: const EdgeInsets.all(24),
      child: Center(
          child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1150),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('API usage and estimated cost',
              style: theme.textTheme.headlineSmall),
          const SizedBox(height: 8),
          Text('Latest service ledger report · Process-wide',
              style: theme.textTheme.bodyMedium),
          if (!connected)
            const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Text(
                    'Service disconnected. Previously reported activity may be incomplete.')),
          if (report == null)
            const Padding(
                padding: EdgeInsets.only(top: 20),
                child: Text('No usage report is available in this session.'))
          else ...[
            const SizedBox(height: 20),
            LayoutBuilder(builder: (context, constraints) {
              final columns = constraints.maxWidth >= 900
                  ? 5
                  : constraints.maxWidth >= 540
                      ? 3
                      : 2;
              final width =
                  (constraints.maxWidth - (columns - 1) * 12) / columns;
              return Wrap(spacing: 12, runSpacing: 12, children: [
                _metric(context, width, 'API calls', _count(total['calls'])),
                _metric(
                    context, width, 'Input tokens', _count(total['prompt'])),
                _metric(
                    context, width, 'Cached input', _count(total['cached'])),
                _metric(context, width, 'Output tokens',
                    _count(total['completion'])),
                _metric(context, width, 'Estimated total',
                    _cost(data['total_cost'], data['currency'])),
              ]);
            }),
            const SizedBox(height: 24),
            Text('Latest completed turn', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('API calls: ${_count(turn['calls'])}'),
                          Text('Input tokens: ${_count(turn['prompt'])}'),
                          Text(
                              'Cached input tokens: ${_count(turn['cached'])}'),
                          Text('Output tokens: ${_count(turn['completion'])}'),
                          Text(
                              'Estimated turn: ${_cost(data['turn_cost'], data['currency'])}'),
                        ]))),
            const SizedBox(height: 24),
            Text('API usage by profile', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            if (profiles.whereType<Map>().isEmpty)
              const Text('No profile usage reported.')
            else
              for (final profile in profiles.whereType<Map>())
                Card(
                    child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Builder(builder: (context) {
                          final values = profile['total'] is Map
                              ? profile['total'] as Map
                              : const {};
                          final pricing = profile['pricing'] is Map
                              ? profile['pricing'] as Map
                              : const {};
                          return Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                    '${_name(profile['participant'])} · ${_name(profile['provider'])} · ${_name(profile['model'])}',
                                    style: theme.textTheme.titleSmall),
                                const SizedBox(height: 6),
                                Text(
                                    'API calls: ${_count(values['calls'])} · Input: ${_count(values['prompt'])} · Cached: ${_count(values['cached'])} · Output: ${_count(values['completion'])}'),
                                Text(
                                    'Estimated profile total: ${_cost(profile['total_cost'], pricing['currency'])}'),
                              ]);
                        }))),
            const SizedBox(height: 16),
            const Text(
                'The ledger is process-wide and is not a billing statement. Turn figures are process-wide and may include concurrent work. Failed or stopped turns may not produce a report. Coding CLI usage is separate.'),
          ],
          const SizedBox(height: 24),
          SessionActivity(events: events, showUsage: false),
        ]),
      )),
    );
  }

  Widget _metric(
          BuildContext context, double width, String label, String value) =>
      SizedBox(
          width: width,
          child: Card(
              child: Padding(
            padding: const EdgeInsets.all(16),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(label, style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: 8),
              Text(value, style: Theme.of(context).textTheme.titleLarge),
            ]),
          )));
}
