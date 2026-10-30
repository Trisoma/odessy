import 'package:flutter/material.dart';
import '../../../app/execution_hosts_controller.dart';

class ExecutionHostsPage extends StatefulWidget {
  const ExecutionHostsPage({super.key, required this.controller});
  final ExecutionHostsController controller;
  @override
  State<ExecutionHostsPage> createState() => _ExecutionHostsPageState();
}

class _ExecutionHostsPageState extends State<ExecutionHostsPage> {
  @override
  void initState() {
    super.initState();
    widget.controller.scan();
  }
  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.controller,
    builder: (context, _) {
      final c = widget.controller;
      return ListView(padding: const EdgeInsets.all(24), children: [
        Text('Connect an execution host', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 12),
        const Text('Open OdessyExecutor on the execution host, then select it here. Its terminal will ask for Y/N approval. No model is needed on the execution host.'),
        const SizedBox(height: 8),
        const Text('Experimental trusted-LAN mode. Device names and IDs are unverified; traffic is unencrypted. Connect only on a network you trust.'),
        const SizedBox(height: 8),
        const Text('Connecting starts a new conversation to load the selected tools. Stop any active turn first.'),
        const SizedBox(height: 16),
        if (c.api == null) const Text('Connect the local coordinator to use execution hosts. No model test is required.'),
        if (c.busy) const LinearProgressIndicator(),
        if (c.message.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(vertical: 12),
            child: Semantics(liveRegion: true, child: Text(c.message))),
        if (c.connection != null) Card(child: ListTile(
          leading: const Icon(Icons.link),
          title: Text('Selected: ${c.connection!['name']}'),
          subtitle: Text('Workspace: ${c.connection!['workspace'] ?? 'Execution host workspace'}'),
          trailing: TextButton(onPressed: c.busy ? null : c.disconnect, child: const Text('Disconnect')))),
        for (final host in c.hosts) Card(child: ListTile(
          leading: const Icon(Icons.computer_outlined),
          title: Text(host['name'] as String),
          subtitle: Text('Device ID: ${host['id']}\n${host['host']} · Nearby advertisement, not verified identity'),
          isThreeLine: true,
          trailing: FilledButton(onPressed: c.busy || c.connection != null ? null : () => c.connect(host['id'] as String),
              child: const Text('Connect')))),
        const SizedBox(height: 12),
        Align(alignment: Alignment.centerLeft, child: OutlinedButton.icon(
          onPressed: c.busy || c.api == null ? null : c.scan,
          icon: const Icon(Icons.refresh), label: const Text('Refresh nearby hosts'))),
      ]);
    });
}
