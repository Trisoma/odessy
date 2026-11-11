import 'package:flutter/material.dart';

import '../../../app/session_controller.dart';

/// Which backend runs new sessions, and what the open session's backend reports.
///
/// Diagnostics surface only: the conversation flow shows no framework detail. The
/// selector applies to the **next** session; the open session keeps the engine it
/// started with, and ending it does not convert its history.
class RuntimeEngine extends StatelessWidget {
  const RuntimeEngine(
      {super.key, required this.session, this.onStartNewSession});
  final DesktopSession session;
  final VoidCallback? onStartNewSession;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: session,
      builder: (context, _) {
        final engines = session.availableEngines;
        final current = session.engine;
        final selected = session.newSessionRuntime ?? '';
        final canStart = session.connected &&
            !session.busy &&
            !session.stopping &&
            session.session != null &&
            session.session!.running != true;
        return Card(
            child: ExpansionTile(
                title: const Text('Runtime engine'),
                subtitle: Text(current == null
                    ? 'No open session'
                    : 'This session: ${current.name}'),
                childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
              Text(
                  'The service default is ${session.engineDefault} unless the installation '
                  'chooses otherwise; the alternative backend is experimental: a turn it '
                  'cannot perform is refused with a reason instead of being emulated. The '
                  'choice applies to the next session; an open session keeps the backend it '
                  'started with.',
                  style: const TextStyle(fontSize: 12)),
              const SizedBox(height: 12),
              if (engines.isEmpty)
                const Text(
                    'The service did not report selectable engines for this connection.',
                    style: TextStyle(fontSize: 12))
              else ...[
                DropdownButton<String>(
                    key: const Key('runtime-engine-select'),
                    isExpanded: true,
                    value: selected,
                    items: [
                      DropdownMenuItem(
                          value: '',
                          child: Text(
                              'Service default (${session.engineDefault})')),
                      for (final name in engines)
                        DropdownMenuItem(
                            value: name,
                            child: Text(name == 'legacy'
                                ? 'legacy'
                                : '$name (experimental)')),
                    ],
                    onChanged: session.busy
                        ? null
                        : (value) => session.selectNewSessionRuntime(
                            value == null || value.isEmpty ? null : value)),
                const SizedBox(height: 8),
                Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton.icon(
                        onPressed: canStart ? onStartNewSession : null,
                        icon: const Icon(Icons.add, size: 18),
                        label: const Text('Start a new session')))
              ],
              if (current != null) ...[
                const Divider(height: 24),
                Text(
                    'Current backend: ${current.name}'
                    '${current.source.isEmpty ? '' : ' · ${_source(current.source)}'}',
                    style: const TextStyle(fontSize: 12)),
                if (current.identity['version'] is String)
                  Text('Runtime version: ${current.identity['version']}',
                      style: const TextStyle(fontSize: 12)),
                Text(
                    current.unsupported.isEmpty
                        ? 'This session reports every listed capability as available.'
                        : 'Not available in this session: '
                            '${current.unsupported.join(', ')}.',
                    style: const TextStyle(fontSize: 12)),
              ],
            ]));
      });

  static String _source(String source) =>
      source == 'explicit' ? 'chosen for this session' : 'installation default';
}
