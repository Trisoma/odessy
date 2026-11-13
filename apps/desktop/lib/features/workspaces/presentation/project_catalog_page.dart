import 'package:flutter/material.dart';
import '../controllers/project_catalog_controller.dart';

/// Registry beside (never instead of) WorkspacePage's reviewed root editor.
class ProjectCatalogPage extends StatefulWidget {
  const ProjectCatalogPage(
      {super.key,
      required this.controller,
      required this.onOpenSession,
      required this.onConfigureWorkspace});
  final ProjectCatalogController controller;
  final ValueChanged<String> onOpenSession;
  final VoidCallback onConfigureWorkspace;
  @override
  State<ProjectCatalogPage> createState() => _ProjectCatalogPageState();
}

class _ProjectCatalogPageState extends State<ProjectCatalogPage> {
  String? actionError;
  Future<String?> _label(String title, String initial) async {
    final input = TextEditingController(text: initial);
    try {
      return await showDialog<String>(
          context: context,
          builder: (context) => AlertDialog(
                  title: Text(title),
                  content: TextField(
                      controller: input,
                      autofocus: true,
                      maxLength: 80,
                      decoration:
                          const InputDecoration(labelText: 'Project name'),
                      onSubmitted: (v) {
                        if (v.trim().isNotEmpty) {
                          Navigator.pop(context, v.trim());
                        }
                      }),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () {
                          if (input.text.trim().isNotEmpty) {
                            Navigator.pop(context, input.text.trim());
                          }
                        },
                        child: const Text('Save name'))
                  ]));
    } finally {
      // Dialog transition may still use its text controller this frame.
      WidgetsBinding.instance.addPostFrameCallback((_) => input.dispose());
    }
  }

  Future<void> _act(Future<void> Function() action) async {
    setState(() => actionError = null);
    try {
      await action();
    } catch (_) {
      if (mounted) {
        setState(() => actionError =
            'Could not complete this action. Refresh and check the current workspace.');
      }
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final busy = c.loading || c.saving;
        return ListView(padding: const EdgeInsets.all(24), children: [
          Text('Your workspaces',
              style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 8),
          const Text(
              'Group live conversations around their configured workspace. Saved groups do not restore a stopped service.'),
          const SizedBox(height: 20),
          Wrap(spacing: 12, runSpacing: 8, children: [
            FilledButton.icon(
                onPressed: busy || c.current?.configuration == null
                    ? null
                    : () => _act(() async {
                          final label = await _label(
                              'Register configured workspace', 'My workspace');
                          if (label != null) await c.registerCurrent(label);
                        }),
                icon: const Icon(Icons.create_new_folder_outlined),
                label: const Text('Register current workspace')),
            OutlinedButton.icon(
                onPressed: widget.onConfigureWorkspace,
                icon: const Icon(Icons.settings_outlined),
                label: const Text('Configure workspace and grants')),
            IconButton(
                onPressed: busy ? null : c.refresh,
                tooltip: 'Refresh projects and live sessions',
                icon: const Icon(Icons.refresh))
          ]),
          const SizedBox(height: 16),
          TextField(
              onChanged: c.search,
              decoration: const InputDecoration(
                  prefixIcon: Icon(Icons.search),
                  labelText: 'Find a live session by ID, engine or profile')),
          if (busy) const LinearProgressIndicator(),
          if (c.error != null || actionError != null)
            Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(c.error ?? actionError!,
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error))),
          if (!busy && c.records.isEmpty)
            const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Text(
                    'No registered workspaces. Register the verified current configuration; this grants no new access.')),
          for (final project in c.records)
            Card(
                margin: const EdgeInsets.only(top: 16),
                child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Row(children: [
                            const Icon(Icons.folder_open_outlined),
                            const SizedBox(width: 12),
                            Expanded(
                                child: Text(project.label,
                                    style: Theme.of(context)
                                        .textTheme
                                        .titleLarge)),
                            PopupMenuButton<String>(
                                tooltip: 'Project metadata actions',
                                enabled: !busy,
                                onSelected: (value) => _act(() async {
                                      if (value == 'rename') {
                                        final label = await _label(
                                            'Rename project', project.label);
                                        if (label != null) {
                                          await c.rename(project.id, label);
                                        }
                                      } else {
                                        await c.removeGroup(project.id);
                                      }
                                    }),
                                itemBuilder: (_) => const [
                                      PopupMenuItem(
                                          value: 'rename',
                                          child: Text('Rename group')),
                                      PopupMenuItem(
                                          value: 'remove',
                                          child: Text(
                                              'Forget group · keep sessions and files'))
                                    ])
                          ]),
                          const SizedBox(height: 8),
                          SelectableText(project.workspace),
                          const SizedBox(height: 8),
                          Text(c.status(project)),
                          const SizedBox(height: 8),
                          for (final sid in project.sessionIds)
                            if (c.query.isEmpty ||
                                c.filteredSessions.any((s) => s.id == sid))
                              ListTile(
                                  contentPadding: EdgeInsets.zero,
                                  leading:
                                      const Icon(Icons.chat_bubble_outline),
                                  title: Text(sid,
                                      overflow: TextOverflow.ellipsis),
                                  subtitle: Text(c.sessions
                                              .any((s) => s.id == sid) &&
                                          c.matches(project)
                                      ? '${c.sessions.firstWhere((s) => s.id == sid).engine} · ${c.sessions.firstWhere((s) => s.id == sid).selectedProfile}'
                                      : 'Session unavailable · no replay'),
                                  trailing: const Icon(Icons.arrow_forward),
                                  enabled: !busy &&
                                      c.matches(project) &&
                                      c.sessions.any(
                                          (s) => s.id == sid && !s.running),
                                  onTap: () => _act(() async {
                                        final selection =
                                            await c.select(project.id, sid);
                                        if (mounted) {
                                          widget.onOpenSession(
                                              selection.sessionId);
                                        }
                                      })),
                          if (project.sessionIds.isEmpty)
                            const Text('No conversations grouped yet.'),
                          if (c.matches(project))
                            Padding(
                                padding: const EdgeInsets.only(top: 8),
                                child: DropdownButtonFormField<String>(
                                    isExpanded: true,
                                    decoration: const InputDecoration(
                                        labelText:
                                            'Group a current live session'),
                                    items: [
                                      for (final session in c.filteredSessions
                                          .where((s) => !project.sessionIds
                                              .contains(s.id)))
                                        DropdownMenuItem(
                                            value: session.id,
                                            child: Text(
                                                '${session.id} · ${session.engine}',
                                                overflow:
                                                    TextOverflow.ellipsis))
                                    ],
                                    onChanged: busy
                                        ? null
                                        : (sid) {
                                            if (sid != null) {
                                              _act(() => c.groupSession(
                                                  project.id, sid));
                                            }
                                          }))
                        ])))
        ]);
      });
}
