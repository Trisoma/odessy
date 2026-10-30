import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';
import '../controllers/runtime_inventory_controller.dart';

class RuntimeInventory extends StatefulWidget {
  const RuntimeInventory({super.key, required this.session});
  final DesktopSession session;
  @override
  State<RuntimeInventory> createState() => _RuntimeInventoryState();
}

class _RuntimeInventoryState extends State<RuntimeInventory> {
  late RuntimeInventoryController controller;
  String filter = '';
  @override
  void initState() {
    super.initState();
    controller = RuntimeInventoryController(widget.session);
  }

  @override
  void didUpdateWidget(covariant RuntimeInventory oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.session != widget.session) {
      controller.dispose();
      controller = RuntimeInventoryController(widget.session);
    }
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final matches = c.settings
            .where((entry) =>
                (entry['name'] as String)
                    .toLowerCase()
                    .contains(filter.toLowerCase()) ||
                (entry['summary'] as String)
                    .toLowerCase()
                    .contains(filter.toLowerCase()))
            .toList();
        return Card(
            child: ExpansionTile(
                title: const Text('Runtime settings inventory'),
                subtitle: const Text('Read-only process environment status'),
                childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
              const Text(
                  'Names and set status only. Values are never displayed. A set status does not prove a value is active or valid.'),
              if (!c.available)
                const Text(
                    'Runtime inventory is unavailable for this connection.'),
              if (c.loading)
                const LinearProgressIndicator(
                    semanticsLabel: 'Loading runtime settings'),
              if (c.error != null) Text(c.error!),
              Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                      onPressed: c.available && !c.loading ? c.refresh : null,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Refresh inventory'))),
              if (c.settings.isNotEmpty) ...[
                TextField(
                    decoration: const InputDecoration(
                        labelText: 'Filter runtime settings'),
                    onChanged: (value) => setState(() => filter = value)),
                Text('${matches.length} of ${c.settings.length} settings'),
                if (matches.isEmpty) const Text('No matching settings.'),
                for (final item in matches)
                  ListTile(
                      title: SelectableText(item['name'] as String),
                      subtitle: Text(
                          '${item['summary']} · ${item['kind']}${item['sensitive'] == true ? ' · sensitive' : ''}'),
                      trailing: Text(item['configured'] == true
                          ? 'Set'
                          : 'Not set or empty')),
              ],
            ]));
      });
}
