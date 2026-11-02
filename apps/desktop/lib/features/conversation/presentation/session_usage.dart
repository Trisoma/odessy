import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

/// Reports only the latest service ledger event; it is not a billing statement.
class SessionUsage extends StatelessWidget {
  const SessionUsage({super.key, required this.events});
  final List<SessionEvent> events;

  String _count(Object? value) =>
      value is int && value >= 0 ? value.toString() : 'Not reported';

  String _cost(Object? value, Object? currency) {
    if (value is! num || !value.isFinite || value < 0) return 'Not reported';
    final unit =
        currency is String && RegExp(r'^[A-Za-z]{1,16}$').hasMatch(currency)
            ? currency
            : null;
    return '$value ${unit ?? '(currency not reported)'}';
  }

  String _name(Object? value) =>
      value is String && value.trim().isNotEmpty ? value : 'Not reported';

  @override
  Widget build(BuildContext context) {
    final reports = events.where((event) => event.type == 'cost');
    final event = reports.isEmpty ? null : reports.last;
    final total = event?.data['total'];
    final values = total is Map ? total : const {};
    final cost = event?.data['total_cost'];
    final costText = _cost(cost, event?.data['currency']);
    final turn = event?.data['turn'];
    final turnValues = turn is Map ? turn : const {};
    final profiles = event?.data['profiles'];
    return Card(
      child: ExpansionTile(
        title: const Text('API usage and estimated cost'),
        subtitle: const Text('Latest service ledger report'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (event == null)
            const Text('No usage report is available in this session.')
          else ...[
            Text('Report event #${event.id}'),
            Text('API calls: ${_count(values['calls'])}'),
            Text('Input tokens: ${_count(values['prompt'])}'),
            Text('Cached input tokens: ${_count(values['cached'])}'),
            Text('Output tokens: ${_count(values['completion'])}'),
            Text('Estimated total: $costText'),
            const SizedBox(height: 12),
            const Text('Latest completed turn'),
            Text('Turn API calls: ${_count(turnValues['calls'])}'),
            Text('Turn input tokens: ${_count(turnValues['prompt'])}'),
            Text('Turn cached input tokens: ${_count(turnValues['cached'])}'),
            Text('Turn output tokens: ${_count(turnValues['completion'])}'),
            Text(
                'Estimated turn: ${_cost(event.data['turn_cost'], event.data['currency'])}'),
            const SizedBox(height: 12),
            const Text('API usage by profile'),
            if (profiles is! List || profiles.whereType<Map>().isEmpty)
              const Text('No profile usage reported.')
            else
              for (final profile in profiles.whereType<Map>())
                Builder(builder: (context) {
                  final totals = profile['total'] is Map
                      ? profile['total'] as Map
                      : const {};
                  final pricing = profile['pricing'] is Map
                      ? profile['pricing'] as Map
                      : const {};
                  return Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                              '${_name(profile['participant'])} · ${_name(profile['provider'])} · ${_name(profile['model'])}'),
                          Text(
                              'API calls: ${_count(totals['calls'])} · Input: ${_count(totals['prompt'])} · Cached: ${_count(totals['cached'])} · Output: ${_count(totals['completion'])}'),
                          Text(
                              'Estimated profile total: ${_cost(profile['total_cost'], pricing['currency'])}'),
                        ]),
                  );
                }),
            const Text(
                'The ledger is process-wide and is not a billing statement. '
                'Turn figures are process-wide and may include concurrent work. '
                'Failed or stopped turns may not produce a report. Coding CLI usage is separate.'),
          ],
        ],
      ),
    );
  }
}
