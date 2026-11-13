import 'package:flutter/material.dart';
import '../controllers/workspace_inspector_controller.dart';

class WorkspaceInspector extends StatelessWidget {
  const WorkspaceInspector(
      {super.key, required this.controller, required this.onClose});
  final WorkspaceInspectorController controller;
  final VoidCallback onClose;

  Widget _code(String text) => SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SelectableText(text,
          style: const TextStyle(
              fontFamily: 'OdessyMono', fontSize: 12, height: 1.6)));

  void _attach(BuildContext context) {
    try {
      controller.attachCurrent();
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text(
              'This file exceeds the attachment budget or is no longer current.')));
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        return Material(
            color: Theme.of(context).colorScheme.surface,
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
              Row(children: [
                for (final panel in InspectorPanel.values)
                  Expanded(
                      child: TextButton(
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
                            InspectorPanel.changes => 'Changes'
                          })))
              ]),
              const Divider(height: 1),
              if (c.loading || c.reading || c.deciding)
                const LinearProgressIndicator(),
              Expanded(
                  child: ListView(padding: const EdgeInsets.all(16), children: [
                if (c.error != null)
                  Text(c.error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
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
                    if (c.outputs.isEmpty)
                      const Text(
                          'No recorded command output is available from this session. Progress titles are not command output.'),
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
