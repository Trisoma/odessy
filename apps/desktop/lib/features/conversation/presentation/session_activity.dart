import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import 'session_usage.dart';

/// Renders service events without inferring tool success or permission.
class SessionActivity extends StatelessWidget {
  const SessionActivity({super.key, required this.events});
  final List<SessionEvent> events;

  @override
  Widget build(BuildContext context) => Card(
        child: ExpansionTile(
          title: const Text('Activity & provenance'),
          subtitle: Text(events.length > 12
              ? 'Latest 12 of ${events.length} available events'
              : '${events.length} available ${events.length == 1 ? 'event' : 'events'}'),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          children: [
            if (events.any((event) => event.type == 'cost'))
              SessionUsage(events: events),
            for (final event in events.reversed.take(12))
              ExpansionTile(
                key: ValueKey(event.id),
                tilePadding: EdgeInsets.zero,
                leading: Icon(
                    event.type == 'error'
                        ? Icons.error_outline
                        : event.type == 'result'
                            ? Icons.terminal
                            : Icons.timeline,
                    size: 18),
                title: Text(switch (event.type) {
                  'progress' => 'Progress update',
                  'result' => 'Turn result',
                  'selection' => 'Model selection',
                  'stopped' => 'Turn stopped',
                  'error' => 'Turn error',
                  'usage' => 'Usage report',
                  'cost' => 'Cost report',
                  'consultation' => 'Consultation',
                  _ => event.type,
                }),
                subtitle: Text('Event #${event.id} · ${event.type}'),
                expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (event.data['message'] is String)
                    Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(event.data['message'] as String)),
                  SelectableText(
                      const JsonEncoder.withIndent('  ').convert(event.data),
                      style: Theme.of(context).textTheme.bodySmall),
                ],
              ),
          ],
        ),
      );
}
