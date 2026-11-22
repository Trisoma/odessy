import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';
import '../controllers/basics_controller.dart';

/// Right click and the keyboard-accessible overflow share the same real actions.
class SavedChatMenu extends StatelessWidget {
  const SavedChatMenu(
      {super.key,
      required this.controller,
      required this.chat,
      required this.child});
  final BasicsController controller;
  final SavedConversation chat;
  final Widget child;
  List<PopupMenuEntry<String>> get items => [
        const PopupMenuItem(value: 'rename', child: Text('Rename')),
        PopupMenuItem(value: 'pin', child: Text(chat.pinned ? 'Unpin' : 'Pin')),
        PopupMenuItem(
            value: 'archive',
            child: Text(chat.archived ? 'Unarchive' : 'Archive')),
        const PopupMenuItem(value: 'export', child: Text('Export JSON')),
        const PopupMenuDivider(),
        const PopupMenuItem(value: 'delete', child: Text('Delete')),
      ];
  Future<void> action(BuildContext context, String value) async {
    final c = controller;
    if (c.busy) return;
    switch (value) {
      case 'rename':
        final input = TextEditingController(text: chat.title);
        final title = await showDialog<String>(
            context: context,
            builder: (ctx) => AlertDialog(
                    title: const Text('Conversation title'),
                    content: TextField(
                        controller: input,
                        autofocus: true,
                        maxLength: 120,
                        decoration: const InputDecoration(labelText: 'Title'),
                        onSubmitted: (v) => Navigator.pop(ctx, v)),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(ctx),
                          child: const Text('Cancel')),
                      FilledButton(
                          onPressed: () => Navigator.pop(ctx, input.text),
                          child: const Text('Save'))
                    ]));
        WidgetsBinding.instance.addPostFrameCallback((_) => input.dispose());
        if (title != null && title.trim().isNotEmpty) {
          await c.updateConversation(chat, title: title.trim());
        }
      case 'pin':
        await c.updateConversation(chat, pinned: !chat.pinned);
      case 'archive':
        await c.updateConversation(chat, archive: !chat.archived);
      case 'delete':
        final confirmed = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
                    title: const Text('Delete saved conversation?'),
                    content: const Text(
                        'Delete this transcript and its service-owned derived data. Workspace documents are not deleted.'),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(ctx, false),
                          child: const Text('Cancel')),
                      FilledButton(
                          onPressed: () => Navigator.pop(ctx, true),
                          child: const Text('Delete'))
                    ]));
        if (confirmed == true) await c.deleteConversation(chat);
      case 'export':
        final data = await c.exportConversation(chat.id);
        if (data == null || !context.mounted) return;
        final text = const JsonEncoder.withIndent('  ').convert(data);
        await showDialog<void>(
            context: context,
            builder: (ctx) => AlertDialog(
                    title: const Text('Conversation export'),
                    content: SizedBox(
                        width: 640,
                        height: 420,
                        child:
                            SingleChildScrollView(child: SelectableText(text))),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(ctx),
                          child: const Text('Close')),
                      FilledButton(
                          onPressed: () async {
                            await Clipboard.setData(ClipboardData(text: text));
                            if (ctx.mounted) Navigator.pop(ctx);
                          },
                          child: const Text('Copy JSON'))
                    ]));
    }
  }

  @override
  Widget build(BuildContext context) => GestureDetector(
      behavior: HitTestBehavior.opaque,
      onSecondaryTapDown: controller.busy
          ? null
          : (event) async {
              final overlay =
                  Overlay.of(context).context.findRenderObject()! as RenderBox;
              final value = await showMenu<String>(
                  context: context,
                  position: RelativeRect.fromRect(
                      Rect.fromLTWH(event.globalPosition.dx,
                          event.globalPosition.dy, 0, 0),
                      Offset.zero & overlay.size),
                  items: items);
              if (value != null && context.mounted) {
                await action(context, value);
              }
            },
      child: Row(children: [
        Expanded(child: child),
        PopupMenuButton<String>(
            tooltip: 'Conversation actions: ${chat.title}',
            enabled: !controller.busy,
            icon: const Icon(Icons.more_horiz, size: 18),
            padding: EdgeInsets.zero,
            onSelected: (value) => action(context, value),
            itemBuilder: (_) => items)
      ]));
}

/// History-only and failed continuation stay readable without attaching a runtime.
class SavedChatView extends StatelessWidget {
  const SavedChatView(
      {super.key, required this.controller, required this.onManage});
  final BasicsController controller;
  final VoidCallback onManage;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller, row = c.selected;
        if (row == null) {
          return const Center(child: Text('Select a saved conversation.'));
        }
        String turn(SavedEvent e) =>
            (e.data['event_id'] as String).split(':').first;
        final settled = {
          for (final e in c.events)
            if (e.kind == 'assistant' ||
                e.kind == 'interrupted' &&
                    e.payload['text'] is String &&
                    (e.payload['text'] as String).isNotEmpty)
              turn(e)
        };
        return Column(children: [
          Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
              child: SavedChatMenu(
                  controller: c,
                  chat: row,
                  child: Text(row.title,
                      style: Theme.of(context).textTheme.titleLarge))),
          Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Text(
                  c.error ??
                      (row.continuable
                          ? 'Saved transcript. Continuation needs a current compatible session and authorized workspace.'
                          : 'Read-only history. This engine does not support continuation.'),
                  key: const Key('saved-chat-status'))),
          TextButton(
              onPressed: onManage,
              child: const Text('History details & workspace binding')),
          if (c.busy) const LinearProgressIndicator(),
          Expanded(
              child: ListView(padding: const EdgeInsets.all(20), children: [
            if (c.before != null)
              Align(
                  child: TextButton(
                      onPressed: c.busy ? null : c.older,
                      child: const Text('Load older messages'))),
            if (c.events.isEmpty) const Text('No recorded messages.'),
            for (final e in c.events)
              if (e.kind != 'assistant_delta' || !settled.contains(turn(e)))
                Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                              e.kind == 'interrupted'
                                  ? 'Interrupted / outcome unknown'
                                  : e.kind,
                              style: Theme.of(context).textTheme.labelMedium),
                          const SizedBox(height: 6),
                          if (e.payload['text'] is String)
                            SelectableText(e.payload['text'] as String)
                          else
                            ExpansionTile(
                                title: Text(e.kind == 'tool_result'
                                    ? 'Recorded result'
                                    : 'Recorded activity'),
                                children: [
                                  SelectableText(
                                      const JsonEncoder.withIndent('  ')
                                          .convert(e.payload))
                                ])
                        ]))
          ])),
        ]);
      });
}
