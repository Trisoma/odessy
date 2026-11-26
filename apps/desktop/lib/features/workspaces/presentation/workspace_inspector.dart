import 'package:flutter/material.dart';
import '../controllers/workspace_inspector_controller.dart';

class WorkspaceInspector extends StatelessWidget {
  const WorkspaceInspector(
      {super.key,
      required this.controller,
      required this.onClose,
      this.activity,
      this.onOpenTerminal,
      this.resumedFromHistory = false,
      this.onOpenHistory});

  /// Integrator supplies a typed terminal-controller command when supported.
  /// Recorded Output remains separate and never launches a shell on tab open.
  final VoidCallback? onOpenTerminal;

  /// True when the live session was resumed from a saved conversation. The
  /// transcript then holds earlier turns while this session holds no recorded
  /// output, which is not the same as a command that failed or ran again.
  final bool resumedFromHistory;

  /// Integrator-supplied read-only route to existing history. It must not resume,
  /// replay or run anything, and must leave the composer draft untouched.
  final VoidCallback? onOpenHistory;
  final Widget? activity;
  final WorkspaceInspectorController controller;
  final VoidCallback onClose;

  Widget _code(String text) => SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SelectableText(text,
          style: const TextStyle(
              fontFamily: 'OdessyMono', fontSize: 12, height: 1.85)));

  void _attach(BuildContext context) {
    try {
      controller.attachCurrent();
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text(
              'This file exceeds the attachment budget or is no longer current.')));
    }
  }

  /// Says why the panel is empty without claiming an outcome. A session that was
  /// resumed from history keeps its earlier replies elsewhere, which is why the
  /// copy separates "not recorded here" from "no command has run".
  Widget _emptyOutput(BuildContext context) {
    final theme = Theme.of(context);
    if (!resumedFromHistory) {
      return Text(
          'No command has run in this session, so there is no output to show. '
          'Progress titles are not command output.',
          key: const Key('recorded-output-empty'),
          style: theme.textTheme.bodyMedium);
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('This live session has no recorded command output yet.',
          key: const Key('recorded-output-empty'),
          style: theme.textTheme.bodyMedium),
      const SizedBox(height: 8),
      Text(
          'Earlier turns belong to the saved history, not to this session. Output '
          'from before it was reopened is not replayed here, and nothing is run again.',
          style: theme.textTheme.bodySmall),
      if (onOpenHistory != null) ...[
        const SizedBox(height: 12),
        TextButton.icon(
            key: const Key('open-recorded-history'),
            onPressed: onOpenHistory,
            icon: const Icon(Icons.history, size: 18, semanticLabel: ''),
            label: const Text('Open recorded history')),
      ],
    ]);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        return Material(
            color: Theme.of(context).colorScheme.surfaceContainerLow,
            child: Column(children: [
              Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 4, 8),
                  child: Row(children: [
                    const Icon(Icons.space_dashboard_outlined, size: 20),
                    const SizedBox(width: 8),
                    Expanded(
                        child: Text(c.descriptor?.label ?? 'Workspace',
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.titleMedium)),
                    IconButton(
                        onPressed: c.loading ? null : c.refresh,
                        tooltip: 'Refresh workspace',
                        icon: const Icon(Icons.refresh)),
                    IconButton(
                        onPressed: onClose,
                        tooltip: 'Close workspace panel',
                        icon: const Icon(Icons.close))
                  ])),
              SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(children: [
                    for (final panel in InspectorPanel.values)
                      TextButton(
                          onPressed: () => c.show(panel),
                          style: TextButton.styleFrom(
                              backgroundColor: c.panel == panel
                                  ? Theme.of(context)
                                      .colorScheme
                                      .secondaryContainer
                                  : null),
                          child: Text(switch (panel) {
                            InspectorPanel.files => 'Files',
                            InspectorPanel.terminal => 'Output',
                            InspectorPanel.changes =>
                              'Changes (${c.changes.length})'
                          })),
                    if (onOpenTerminal != null)
                      TextButton(
                          onPressed: onOpenTerminal,
                          child: const Text('Terminal')),
                  ])),
              const Divider(height: 1),
              if (c.loading || c.reading || c.deciding)
                const LinearProgressIndicator(),
              Expanded(
                  child: ListView(padding: const EdgeInsets.all(16), children: [
                if (c.error != null)
                  Text(c.error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
                if (c.panel == InspectorPanel.terminal && activity != null)
                  activity!,
                if (c.descriptor == null && !c.loading)
                  const Text(
                      'Select an authorized local workspace. Monitoring-only computers cannot provide files or commands.'),
                if (c.descriptor != null) ...[
                  Text('Session ${c.descriptor!.sessionId}',
                      style: Theme.of(context).textTheme.labelSmall),
                  const SizedBox(height: 16),
                  if (c.panel == InspectorPanel.files) ...[
                    if (!c.descriptor!.readFiles)
                      const Text('File access is unavailable.'),
                    if (c.tree != null) ...[
                      Row(children: [
                        Expanded(child: Text(c.tree!.path)),
                        if (c.tree!.path != '.')
                          IconButton(
                              tooltip: 'Workspace root',
                              icon: const Icon(Icons.home_outlined),
                              onPressed: c.reading
                                  ? null
                                  : () => c.open('.', directory: true))
                      ]),
                      if (c.tree!.entries.isEmpty)
                        const Text('No visible files in this directory.'),
                      for (final entry in c.tree!.entries)
                        ListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            leading: Icon(entry.kind == 'directory'
                                ? Icons.folder_outlined
                                : Icons.description_outlined),
                            title: Text(entry.name,
                                overflow: TextOverflow.ellipsis),
                            onTap: c.reading
                                ? null
                                : () => c.open(entry.path,
                                    directory: entry.kind == 'directory')),
                      if (c.tree!.truncated)
                        const Text('Directory limited to 200 scanned entries.'),
                    ],
                    if (c.file != null) ...[
                      const Divider(),
                      SelectableText(c.file!.path),
                      const SizedBox(height: 8),
                      _code(c.file!.text),
                      const SizedBox(height: 12),
                      OutlinedButton.icon(
                          onPressed: () => _attach(context),
                          icon: const Icon(Icons.attach_file),
                          label: const Text('Attach reviewed text')),
                    ],
                    if (c.attachments.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      const Text(
                          'Text context · rechecked before explicit send'),
                      Wrap(spacing: 8, children: [
                        for (final a in c.attachments)
                          InputChip(
                              label: Text(a.file.path),
                              onDeleted: () => c.removeAttachment(a.file.path))
                      ])
                    ]
                  ],
                  if (c.panel == InspectorPanel.terminal) ...[
                    const Text(
                        'Recorded command output · read only. Opening this panel runs nothing.'),
                    const SizedBox(height: 16),
                    if (c.outputs.isEmpty) _emptyOutput(context),
                    for (final output in c.outputs)
                      Padding(
                          padding: const EdgeInsets.only(bottom: 20),
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                Text('${output.toolName} · ${output.state}',
                                    style:
                                        Theme.of(context).textTheme.titleSmall),
                                SelectableText(
                                    '${output.turnId} / ${output.callId}',
                                    style:
                                        Theme.of(context).textTheme.labelSmall),
                                if (output.stdout.isNotEmpty)
                                  _code(output.stdout),
                                if (output.stderr.isNotEmpty) ...[
                                  const Text('stderr'),
                                  _code(output.stderr)
                                ],
                                Text(
                                    'Exit: ${output.exitCode ?? 'unknown'}${output.truncated ? ' · output truncated' : ''}')
                              ]))
                  ],
                  if (c.panel == InspectorPanel.changes) ...[
                    if (c.changes.isEmpty)
                      const Text(
                          'No bound pending file changes. Existing approvals remain in the conversation.'),
                    for (final change in c.changes)
                      Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Text(change.path,
                                style: Theme.of(context).textTheme.titleSmall),
                            Text('Pending request ${change.requestId}',
                                style: Theme.of(context).textTheme.labelSmall),
                            const SizedBox(height: 12),
                            Container(
                                padding: const EdgeInsets.all(8),
                                color: Theme.of(context)
                                    .colorScheme
                                    .errorContainer,
                                child: _code(change.beforeText
                                    .split('\n')
                                    .map((line) => '- $line')
                                    .join('\n'))),
                            Container(
                                padding: const EdgeInsets.all(8),
                                color: Theme.of(context)
                                    .colorScheme
                                    .secondaryContainer,
                                child: _code(change.afterText
                                    .split('\n')
                                    .map((line) => '+ $line')
                                    .join('\n'))),
                            const SizedBox(height: 12),
                            if (c.canDecide(change))
                              Wrap(spacing: 8, children: [
                                OutlinedButton(
                                    onPressed: () => c.decide(change, false),
                                    child: const Text('Deny exact request')),
                                FilledButton(
                                    onPressed: () => c.decide(change, true),
                                    child: const Text('Approve exact request'))
                              ])
                            else
                              const Text(
                                  'Preview only · exact dispatch integration is unavailable.'),
                          ])
                  ]
                ]
              ]))
            ]));
      });
}
