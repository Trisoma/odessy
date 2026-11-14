import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart' show SavedEvent;
import '../controllers/basics_controller.dart';
import '../../conversation/presentation/interaction_card.dart';

/// Advanced tools stay outside the composer; no tab starts a process or model.
class BasicsPage extends StatefulWidget {
  const BasicsPage(
      {super.key,
      required this.controller,
      required this.onResume,
      this.initialTab = 'History',
      this.workspaceLabel});
  final BasicsController controller;
  final Future<void> Function() onResume;
  final String initialTab;
  final String? workspaceLabel;
  @override
  State<BasicsPage> createState() => _BasicsPageState();
}

class _BasicsPageState extends State<BasicsPage> {
  late String tab;
  final search = TextEditingController(),
      memory = TextEditingController(),
      path = TextEditingController(),
      query = TextEditingController(),
      terminalInput = TextEditingController();
  final columns = TextEditingController(text: '80'),
      rows = TextEditingController(text: '24');
  BasicsController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    tab = widget.initialTab;
  }

  @override
  void didUpdateWidget(covariant BasicsPage old) {
    super.didUpdateWidget(old);
    if (old.initialTab != widget.initialTab) tab = widget.initialTab;
  }

  @override
  void dispose() {
    for (final input in [
      search,
      memory,
      path,
      query,
      terminalInput,
      columns,
      rows
    ]) {
      input.dispose();
    }
    super.dispose();
  }

  Future<String?> _text(String title, String initial, {int max = 120}) async {
    final input = TextEditingController(text: initial);
    try {
      return await showDialog<String>(
          context: context,
          builder: (ctx) => AlertDialog(
                  title: Text(title),
                  content: SizedBox(
                      width: 420,
                      child: TextField(
                          controller: input,
                          autofocus: true,
                          maxLength: max,
                          maxLines: max > 120 ? 4 : 1,
                          decoration: InputDecoration(labelText: title))),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(ctx),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () {
                          if (input.text.trim().isNotEmpty) {
                            Navigator.pop(ctx, input.text.trim());
                          }
                        },
                        child: const Text('Save'))
                  ]));
    } finally {
      WidgetsBinding.instance.addPostFrameCallback((_) => input.dispose());
    }
  }

  Future<bool> _confirm(String title, String message) async =>
      await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
                  title: Text(title),
                  content: SingleChildScrollView(child: Text(message)),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(ctx, false),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () => Navigator.pop(ctx, true),
                        child: const Text('Confirm'))
                  ])) ??
      false;
  Future<void> _export() async {
    final value = await c.exportSelected();
    if (value == null || !mounted) return;
    final encoded = const JsonEncoder.withIndent('  ').convert(value);
    await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('Versioned conversation export'),
                content: SizedBox(
                    width: 700,
                    height: 450,
                    child:
                        SingleChildScrollView(child: SelectableText(encoded))),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('Close')),
                  FilledButton.icon(
                      onPressed: () async {
                        await Clipboard.setData(ClipboardData(text: encoded));
                        if (ctx.mounted) Navigator.pop(ctx);
                      },
                      icon: const Icon(Icons.copy),
                      label: const Text('Copy JSON'))
                ]));
  }

  Widget _button(String label, VoidCallback? action, {IconData? icon}) => Align(
      alignment: Alignment.centerLeft,
      child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: OutlinedButton.icon(
              onPressed: c.busy ? null : action,
              icon: Icon(icon ?? Icons.arrow_forward, size: 17),
              label: Text(label))));
  Widget _unavailable(String feature) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Text(
          '${feature[0].toUpperCase()}${feature.substring(1)} is unavailable in this service. Connect to a service that supports this feature, then refresh.'));
  Widget _heading(String text) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Text(text, style: Theme.of(context).textTheme.titleMedium));
  Widget _json(Object? value) =>
      SelectableText(const JsonEncoder.withIndent('  ').convert(value));
  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) => Column(children: [
            Padding(
                padding: const EdgeInsets.fromLTRB(20, 16, 12, 8),
                child: Row(children: [
                  Expanded(
                      child: Text('History & tools',
                          style: Theme.of(context).textTheme.titleLarge)),
                  IconButton(
                      onPressed: c.busy ? null : c.refresh,
                      tooltip: 'Refresh history and capabilities',
                      icon: const Icon(Icons.refresh))
                ])),
            SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(children: [
                  for (final name in [
                    'History',
                    'Context',
                    'Memory',
                    'Documents',
                    'Terminal',
                    'Checkpoints'
                  ])
                    Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 4),
                        child: ChoiceChip(
                            label: Text(name),
                            selected: tab == name,
                            onSelected: (_) => setState(() => tab = name)))
                ])),
            if (c.busy) const LinearProgressIndicator(),
            Expanded(
                child: Align(
                    alignment: Alignment.topCenter,
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 960),
                        child: ListView(
                            padding: const EdgeInsets.fromLTRB(28, 24, 28, 32),
                            children: [
                              for (final entry in c.session.session?.pending ??
                                  const <Map<String, dynamic>>[])
                                InteractionCard(
                                    key: ValueKey(
                                        (c.session.session?.id, entry['id'])),
                                    entry: entry,
                                    controller: c.session),
                              if (c.error != null)
                                Semantics(
                                    liveRegion: true,
                                    child: Padding(
                                        padding:
                                            const EdgeInsets.only(bottom: 12),
                                        child: Text(c.error!,
                                            style: TextStyle(
                                                color: Theme.of(context)
                                                    .colorScheme
                                                    .error)))),
                              if (c.notice != null)
                                Semantics(
                                    liveRegion: true,
                                    child: Padding(
                                        padding:
                                            const EdgeInsets.only(bottom: 12),
                                        child: Text(c.notice!))),
                              if (c.capabilities == null)
                                const Text(
                                    'Connect and refresh to discover supported capabilities.'),
                              ...switch (tab) {
                                'Context' => _context(),
                                'Memory' => _memory(),
                                'Documents' => _documents(),
                                'Terminal' => _terminal(),
                                'Checkpoints' => _checkpoints(),
                                _ => _history()
                              },
                            ]))))
          ]));
  List<Widget> _history() => [
        const Text(
            'Saved history survives service restart. Browsing never launches a runtime or replays actions. Project grouping grants no workspace access.'),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
            key: ValueKey((c.projectId, c.projects.length)),
            initialValue: c.projectId ?? '',
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Durable project'),
            items: [
              const DropdownMenuItem(value: '', child: Text('All projects')),
              for (final p in c.projects)
                DropdownMenuItem(
                    value: p.id,
                    child: Text(p.label, overflow: TextOverflow.ellipsis))
            ],
            onChanged:
                c.busy ? null : (v) => c.filter(project: v == '' ? null : v)),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 8, children: [
          _button(
              'New project',
              !c.supports('conversations')
                  ? null
                  : () async {
                      final label = await _text('Project name', '');
                      if (label != null) await c.createProject(label);
                    },
              icon: Icons.create_new_folder_outlined),
          _button(
              'New saved chat',
              c.projectId == null || !c.supports('conversations')
                  ? null
                  : c.createChat,
              icon: Icons.add_comment_outlined),
          FilterChip(
              label: const Text('Archived'),
              selected: c.archived,
              onSelected: c.busy
                  ? null
                  : (v) => c.filter(project: c.projectId, showArchived: v)),
        ]),
        const SizedBox(height: 12),
        TextField(
            controller: search,
            maxLength: 512,
            decoration: InputDecoration(
                labelText: 'Search full transcript',
                suffixIcon: IconButton(
                    tooltip: 'Search saved transcripts',
                    onPressed: c.busy ? null : () => c.search(search.text),
                    icon: const Icon(Icons.search))),
            onSubmitted: c.busy ? null : c.search),
        if (c.query.isNotEmpty) ...[
          _heading('Search results'),
          if (c.matches.isEmpty) const Text('No transcript matches.'),
          for (final m in c.matches)
            ListTile(
                title: Text(m.snippet),
                subtitle: Text('Message ${m.sequence} · ${m.conversationId}'),
                onTap: c.busy ? null : () => c.open(m.conversationId)),
          if (c.searchCursor != null)
            _button('More search results', () => c.search(c.query, more: true)),
        ],
        if (!c.supports('conversations')) _unavailable('conversations'),
        if (c.supports('conversations') && c.conversations.isEmpty && !c.busy)
          const Padding(
              padding: EdgeInsets.all(16),
              child: Text('No saved conversations in this view.')),
        for (final row in c.conversations)
          ListTile(
              selected: c.selected?.id == row.id,
              leading:
                  Icon(row.pinned ? Icons.push_pin : Icons.chat_bubble_outline),
              title: Text(row.title),
              subtitle: Text(
                  '${row.engine} · ${row.profile} · ${row.continuable ? 'Resumable with fresh authority' : 'Read-only history'}'),
              trailing:
                  row.archived ? const Icon(Icons.archive_outlined) : null,
              onTap: c.busy ? null : () => c.open(row.id)),
        if (c.cursor != null) _button('More conversations', c.more),
        if (c.selected != null) ..._selected(),
      ];
  List<Widget> _selected() {
    final row = c.selected!;
    return [
      const Divider(height: 32),
      _heading(row.title),
      SelectableText(
          'Saved ${row.id} · revision ${row.revision}\n${row.continuable ? 'Resume is explicit. Grants, jobs and audio are never restored.' : 'Read-only: this engine does not support continuation.'}'),
      const SizedBox(height: 12),
      Wrap(spacing: 8, runSpacing: 8, children: [
        _button('Rename', () async {
          final title = await _text('Conversation title', row.title);
          if (title != null) await c.update(title: title);
        }, icon: Icons.edit_outlined),
        _button(
            row.pinned ? 'Unpin' : 'Pin', () => c.update(pinned: !row.pinned),
            icon: Icons.push_pin_outlined),
        _button(row.archived ? 'Unarchive' : 'Archive',
            () => c.update(archive: !row.archived),
            icon: Icons.archive_outlined),
        _button('Export JSON', _export, icon: Icons.ios_share),
        _button('Delete', () async {
          if (await _confirm('Delete saved conversation?',
              'Delete this transcript and its service-owned derived data. This does not delete workspace documents.')) {
            await c.deleteSelected();
          }
        }, icon: Icons.delete_outline),
        _button(
            'Bind current workspace',
            !c.live || !row.continuable
                ? null
                : () async {
                    if (await _confirm('Bind workspace identity?',
                        'Bind this unbound saved chat to the currently authorized workspace:\n${widget.workspaceLabel ?? c.mode?.scopeLabel ?? 'Workspace identity must be verified by the service'}\n\nNo permissions are copied. Already-bound history cannot be rebound.')) {
                      await c.bindSelected();
                    }
                  },
            icon: Icons.link),
        _button(
            'Resume conversation',
            !c.live ||
                    !row.continuable ||
                    c.session.busy ||
                    c.session.session?.running == true
                ? null
                : widget.onResume,
            icon: Icons.play_arrow),
      ]),
      _heading('Transcript and recorded activity'),
      if (c.before != null) _button('Load older messages', c.older),
      if (c.events.isEmpty) const Text('No recorded messages.'),
      ..._transcript(),
    ];
  }

  List<Widget> _transcript() {
    // Settled text supersedes partial deltas for the same turn. Never display
    // duplicated assistant text, and retain interrupted partials honestly.
    String turn(SavedEvent e) =>
        (e.data['event_id'] as String).split(':').first;
    final settled = <String>{
      for (final e in c.events)
        if (e.kind == 'assistant' ||
            e.kind == 'interrupted' &&
                e.payload['text'] is String &&
                (e.payload['text'] as String).isNotEmpty)
          turn(e)
    };
    return [
      for (final e in c.events)
        if (e.kind != 'assistant_delta' || !settled.contains(turn(e)))
          Card(
              child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                            '#${e.sequence} · ${e.kind == 'interrupted' ? 'Interrupted / outcome unknown' : e.kind}',
                            style: Theme.of(context).textTheme.labelMedium),
                        const SizedBox(height: 6),
                        if (e.payload['text'] is String)
                          SelectableText(e.payload['text'] as String)
                        else
                          ExpansionTile(
                              tilePadding: EdgeInsets.zero,
                              title: Text(e.kind == 'tool_result'
                                  ? 'Recorded result · never executed here'
                                  : 'Recorded details'),
                              children: [_json(e.payload)]),
                      ])))
    ];
  }

  List<Widget> _context() => [
        const Text(
            'Inspect only the last context actually used by the service. Opening this page never prepares context or calls a model.'),
        _heading(
            'Selected saved conversation: ${c.effectiveConversationId ?? 'none'}'),
        _button('Inspect used context',
            c.supports('context') ? c.loadContext : null,
            icon: Icons.visibility_outlined),
        if (!c.supports('context')) _unavailable('context'),
        if (c.contextUsed != null) ...[
          _heading(
              'Budget ${c.contextUsed!.data['chars_used']} / ${c.contextUsed!.data['budget_chars']} characters'),
          Text('Method: ${c.contextUsed!.data['method']}'),
          if (c.contextUsed!.data['summary'] == null)
            const Text('No summary was used.')
          else ...[
            _heading('Summary used'),
            SelectableText(c.contextUsed!.data['summary']['text'] as String),
            Text(
                'Method: ${c.contextUsed!.data['summary']['method']} · through message ${c.contextUsed!.data['summary']['through_sequence']} · revision ${c.contextUsed!.data['summary']['revision']}'),
          ],
          _heading('Injected memory facts'),
          if ((c.contextUsed!.data['memory_ids'] as List).isEmpty)
            const Text('No memory facts were used.'),
          for (final id in c.contextUsed!.data['memory_ids'] as List)
            SelectableText(id as String),
          _heading('Retrieved sources actually used'),
          for (final citation in c.contextUsed!.data['citations'] as List)
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                              '${citation['path']}:${citation['start_line']}–${citation['end_line']}'),
                          SelectableText(citation['text'] as String),
                          Text('Source hash: ${citation['source_hash']}'),
                        ]))),
          ExpansionTile(
              title: const Text('Context receipt and source hashes'),
              children: [_json(c.contextUsed!.data)]),
        ]
      ];
  List<Widget> _memory() => [
        const Text(
            'Explicit project facts only. Enabled facts may enter subsequent bounded model requests as untrusted context. These are separate from preferences and permissions.'),
        _heading(
            'Project: ${c.effectiveProjectId ?? 'select a saved project or chat'}'),
        _button('Refresh memory', c.supports('memory') ? c.loadMemory : null,
            icon: Icons.refresh),
        if (!c.supports('memory')) _unavailable('memory'),
        TextField(
            controller: memory,
            maxLength: 2000,
            maxLines: 3,
            decoration:
                const InputDecoration(labelText: 'New explicit memory fact')),
        _button(
            'Save fact',
            c.supports('memory')
                ? () async {
                    await c.saveMemory(memory.text);
                    if (c.error == null && mounted) memory.clear();
                  }
                : null,
            icon: Icons.add),
        for (final fact in c.facts)
          Card(
              child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        SelectableText(fact.text),
                        Wrap(spacing: 8, children: [
                          Semantics(
                              label: 'Enable memory fact',
                              child: Switch.adaptive(
                                value: fact.enabled,
                                onChanged: c.busy
                                    ? null
                                    : (v) => c.toggleMemory(fact, v),
                              )),
                          _button('Edit fact', () async {
                            final text = await _text(
                                'Edit memory fact', fact.text,
                                max: 2000);
                            if (text != null) {
                              await c.saveMemory(text, fact: fact);
                            }
                          }, icon: Icons.edit_outlined),
                          _button('Delete fact', () async {
                            if (await _confirm(
                                'Delete memory fact?', fact.text)) {
                              await c.deleteMemory(fact);
                            }
                          }, icon: Icons.delete_outline),
                        ])
                      ]))),
        if (c.supports('memory') && c.facts.isEmpty)
          const Text('No facts loaded. Refresh to inspect this project.'),
      ];
  List<Widget> _documents() => [
        const Text(
            'Local lexical retrieval is opt-in. Index only an allowed relative file in the current granted workspace. Source drift and permission revocation are checked by the service.'),
        _heading('Project: ${c.effectiveProjectId ?? 'none'}'),
        _button('Refresh indexed documents',
            c.supports('retrieval') ? c.loadDocuments : null,
            icon: Icons.refresh),
        if (!c.supports('retrieval')) _unavailable('retrieval'),
        TextField(
            controller: path,
            decoration: const InputDecoration(
                labelText: 'Workspace-relative document path')),
        _button(
            'Index selected file',
            c.supports('retrieval') && c.live
                ? () async {
                    if (await _confirm('Index this file?',
                        'Opt in to storing bounded text from ${path.text} for this project. This does not grant file access.')) {
                      await c.index(path.text);
                    }
                  }
                : null,
            icon: Icons.add),
        for (final d in c.documents)
          Card(
              child: ListTile(
                  title: Text(d.path),
                  subtitle: Text(
                      '${d.data['status']} · ${d.data['chunks']} chunks${d.data['error'] == null ? '' : ' · ${d.data['error']}'}'),
                  trailing: PopupMenuButton<String>(
                      tooltip: 'Indexed document actions',
                      enabled: !c.busy,
                      onSelected: (v) async {
                        if (v == 'refresh') {
                          await c.index(d.path);
                        } else if (await _confirm('Remove from index?',
                            'Remove stored chunks for ${d.path}. The workspace file is retained.')) {
                          await c.removeDocument(d);
                        }
                      },
                      itemBuilder: (_) => [
                            PopupMenuItem(
                                value: 'refresh',
                                enabled: c.live,
                                child: const Text('Refresh source')),
                            const PopupMenuItem(
                                value: 'remove',
                                child: Text('Remove index · keep file'))
                          ]))),
        const SizedBox(height: 16),
        TextField(
            controller: query,
            maxLength: 512,
            decoration:
                const InputDecoration(labelText: 'Find grounded excerpts')),
        _button(
            'Retrieve citations',
            c.supports('retrieval') && c.live
                ? () => c.retrieve(query.text)
                : null,
            icon: Icons.search),
        if (c.retrieval != null) ...[
          _heading('Retrieved citations (not a model answer)'),
          for (final item in c.retrieval!['citations'] as List)
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                              '${item['path']}:${item['start_line']}–${item['end_line']}'),
                          SelectableText(item['text'] as String),
                          Text('Source hash: ${item['source_hash']}')
                        ]))),
          Text(
              'Stale documents excluded: ${(c.retrieval!['stale_document_ids'] as List).length}')
        ],
      ];
  List<Widget> _terminal() => [
        const Text(
            'Interactive Terminal is separate from recorded Output in the workspace inspector. Creating a local shell requires its own service authorization. Input can cause effects within that shell grant. Opening this page runs nothing.'),
        _heading('Live session: ${c.session.session?.id ?? 'none'}'),
        if (!c.supports('terminal')) _unavailable('terminal'),
        Wrap(spacing: 8, runSpacing: 8, children: [
          _button(
              'Create local terminal',
              c.supports('terminal') && c.live && c.terminal?.running != true
                  ? c.createTerminal
                  : null,
              icon: Icons.terminal),
          _button('Refresh terminal output',
              c.terminal == null ? null : c.readTerminal,
              icon: Icons.refresh),
          _button(
              'Interrupt',
              c.terminal?.running == true
                  ? () => c.terminalAction('interrupt')
                  : null,
              icon: Icons.stop_circle_outlined),
          _button('Close terminal',
              c.terminal == null ? null : () => c.terminalAction('close'),
              icon: Icons.close),
        ]),
        if (c.terminal != null)
          Text(
              'Status: ${c.terminal!.data['status']} · revision ${c.terminal!.revision} · exit ${c.terminal!.data['exit_code'] ?? 'not reported'}'),
        if (c.outputTruncated)
          const Text(
              'Earlier output was truncated. Displayed output is bounded.'),
        Container(
            padding: const EdgeInsets.all(14),
            margin: const EdgeInsets.symmetric(vertical: 12),
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: SelectableText(
                c.terminalText.isEmpty
                    ? 'No terminal output received.'
                    : _safeTerminal(c.terminalText),
                style:
                    const TextStyle(fontFamily: 'OdessyMono', fontSize: 12))),
        TextField(
            controller: terminalInput,
            maxLength: 16384,
            maxLines: 3,
            decoration: const InputDecoration(
                labelText:
                    'Interactive shell input · sent exactly as entered')),
        _button(
            'Send terminal input',
            c.terminal?.running == true
                ? () async {
                    await c.input(terminalInput.text);
                    if (c.error == null && mounted) terminalInput.clear();
                  }
                : null,
            icon: Icons.send_outlined),
        Wrap(spacing: 12, runSpacing: 8, children: [
          SizedBox(
              width: 100,
              child: TextField(
                  controller: columns,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Columns'))),
          SizedBox(
              width: 100,
              child: TextField(
                  controller: rows,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Rows'))),
          _button(
              'Resize terminal',
              c.terminal?.running == true
                  ? () {
                      final col = int.tryParse(columns.text),
                          row = int.tryParse(rows.text);
                      if (col == null ||
                          row == null ||
                          col < 20 ||
                          col > 400 ||
                          row < 5 ||
                          row > 200) {
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                            content: Text(
                                'Columns must be 20–400; rows must be 5–200.')));
                        return;
                      }
                      c.terminalAction('resize', columns: col, rows: row);
                    }
                  : null,
              icon: Icons.aspect_ratio)
        ]),
      ];
  String _safeTerminal(String text) => text
      .replaceAll(RegExp(r'\x1b\[[0-?]*[ -/]*[@-~]'), '')
      .replaceAll(RegExp(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]'), '');
  List<Widget> _checkpoints() => [
        const Text(
            'Local Git file checkpoints only. Restore affects captured supported paths, creates a safety checkpoint, and cannot undo external actions. Review the exact preview before confirming.'),
        if (!c.supports('checkpoints')) _unavailable('checkpoints'),
        Wrap(spacing: 8, runSpacing: 8, children: [
          _button('Refresh checkpoints',
              c.supports('checkpoints') && c.live ? c.loadCheckpoints : null,
              icon: Icons.refresh),
          _button(
              'Create checkpoint',
              c.supports('checkpoints') && c.live
                  ? () async {
                      final label = await _text('Checkpoint label', '');
                      if (label != null) await c.createCheckpoint(label);
                    }
                  : null,
              icon: Icons.save_outlined)
        ]),
        for (final cp in c.checkpoints)
          ListTile(
              title: Text(cp.label),
              subtitle: Text(
                  '${cp.data['created_at']} · revision ${cp.data['revision']}'),
              trailing: IntrinsicWidth(
                  child: _button('Preview', () => c.previewCheckpoint(cp),
                      icon: Icons.visibility_outlined))),
        if (c.preview != null) ...[
          _heading('Exact restore preview'),
          Text(
              'Checkpoint: ${c.preview!.data['checkpoint_id']} · revision ${c.preview!.data['expected_revision']}'),
          for (final change in c.preview!.data['changes'] as List)
            ListTile(
                leading: const Icon(Icons.description_outlined),
                title: Text(change is Map ? '${change['path']}' : '$change'),
                subtitle: change is Map
                    ? Text('${change['status'] ?? 'changed'}')
                    : null),
          if ((c.preview!.data['unsupported'] as List).isNotEmpty) ...[
            _heading('Unsupported paths · excluded from restore'),
            for (final item in c.preview!.data['unsupported'] as List)
              Text('$item'),
          ],
          ExpansionTile(
              title: const Text('Exact restore receipt'),
              children: [_json(c.preview!.data)]),
          _button('Confirm restore', () async {
            final exact = c.preview!;
            if (await _confirm('Restore this exact checkpoint?',
                'Restore ${exact.data['checkpoint_id']} using the displayed content-bound preview?\nA separate service authorization may still be required. External actions are not undone.')) {
              await c.restoreCheckpoint(exact);
            }
          }, icon: Icons.restore)
        ],
      ];
}
