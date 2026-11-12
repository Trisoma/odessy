import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/execution_hosts_controller.dart';
import '../../../app/ssh_connection_controller.dart';

class ExecutionHostsPage extends StatefulWidget {
  const ExecutionHostsPage({super.key, required this.controller, this.ssh});
  final ExecutionHostsController controller;
  final SshConnectionController? ssh;
  @override
  State<ExecutionHostsPage> createState() => _ExecutionHostsPageState();
}

class _ExecutionHostsPageState extends State<ExecutionHostsPage> {
  @override
  void initState() {
    super.initState();
    widget.controller.scan();
    widget.ssh?.load();
  }

  @override
  void didUpdateWidget(covariant ExecutionHostsPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.controller, widget.controller)) {
      widget.controller.scan();
    }
    if (!identical(oldWidget.ssh, widget.ssh)) {
      widget.ssh?.load();
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return ListView(padding: const EdgeInsets.all(24), children: [
          Text('Connect a computer',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text(
              'Use an existing OpenSSH alias. This build supports read-only monitoring over SSH; execution is not available yet.'),
          const SizedBox(height: 12),
          if (widget.ssh == null)
            const Card(
                child: Padding(
                    padding: EdgeInsets.all(16),
                    child: Text(
                        'SSH setup needs the local Odessy service. Connect the service to inspect your aliases.')))
          else
            _SshSection(controller: widget.ssh!),
          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 12),
          Text('Nearby execution hosts (trusted LAN)',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          const Text(
              'Open OdessyExecutor on the execution host, then select it here. Its terminal will ask for Y/N approval. No model is needed on the execution host.'),
          const SizedBox(height: 8),
          const Text(
              'Experimental trusted-LAN mode. Device names and IDs are unverified; traffic is unencrypted. Connect only on a network you trust.'),
          const SizedBox(height: 8),
          const Text(
              'Connecting starts a new conversation to load the selected tools. Stop any active turn first.'),
          const SizedBox(height: 16),
          if (c.api == null)
            const Text(
                'Connect the local coordinator to use execution hosts. No model test is required.'),
          if (c.busy) const LinearProgressIndicator(),
          if (c.message.isNotEmpty)
            Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Semantics(liveRegion: true, child: Text(c.message))),
          if (c.connection != null)
            Card(
                child: ListTile(
                    leading: const Icon(Icons.link),
                    title: Text('Selected: ${c.connection!['name']}'),
                    subtitle: Text(
                        'Workspace: ${c.connection!['workspace'] ?? 'Execution host workspace'}'),
                    trailing: TextButton(
                        onPressed: c.busy ? null : c.disconnect,
                        child: const Text('Disconnect')))),
          for (final host in c.hosts)
            Card(
                child: ListTile(
                    leading: const Icon(Icons.computer_outlined),
                    title: Text(host['name'] as String),
                    subtitle: Text(
                        'Device ID: ${host['id']}\n${host['host']} · Nearby advertisement, not verified identity'),
                    isThreeLine: true,
                    trailing: FilledButton(
                        onPressed: c.busy || c.connection != null
                            ? null
                            : () => c.connect(host['id'] as String),
                        child: const Text('Connect')))),
          const SizedBox(height: 12),
          Align(
              alignment: Alignment.centerLeft,
              child: OutlinedButton.icon(
                  onPressed: c.busy || c.api == null ? null : c.scan,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Refresh nearby hosts'))),
        ]);
      });
}

class _SshSection extends StatelessWidget {
  const _SshSection({required this.controller});
  final SshConnectionController controller;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        if (c.api == null) {
          return const Card(
              child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Text(
                      'Connect the local Odessy service to inspect and test SSH aliases. No model key is required.')));
        }
        final identity = c.identity;
        final test = c.test;
        final status = c.status;
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      const Icon(Icons.terminal_outlined),
                      const SizedBox(width: 8),
                      Text('Using SSH',
                          style: Theme.of(context).textTheme.titleMedium),
                    ]),
                    const SizedBox(height: 8),
                    const Text(
                        'Select a Host entry from your OpenSSH configuration. Keys, agent and known_hosts stay yours.'),
                    const SizedBox(height: 12),
                    if (c.aliases.isEmpty)
                      const Text(
                          'No aliases found. Add a Host entry to ~/.ssh/config, then refresh.')
                    else
                      DropdownButtonFormField<String>(
                        key: ValueKey('ssh-alias-${c.selected}'),
                        initialValue: c.selected,
                        decoration:
                            const InputDecoration(labelText: 'SSH alias'),
                        items: [
                          for (final alias in c.aliases)
                            DropdownMenuItem(value: alias, child: Text(alias))
                        ],
                        onChanged: c.busy
                            ? null
                            : (value) {
                                if (value != null) c.select(value);
                              },
                      ),
                    const SizedBox(height: 10),
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      OutlinedButton.icon(
                          onPressed: c.busy ? null : c.refreshAliases,
                          icon: const Icon(Icons.refresh),
                          label: const Text('Refresh aliases')),
                      OutlinedButton(
                          onPressed: c.busy || c.selected == null
                              ? null
                              : () => c.inspect(c.selected!),
                          child: const Text('Recheck identity')),
                      FilledButton(
                          onPressed: c.busy ||
                                  c.selected == null ||
                                  !c.known ||
                                  c.changed
                              ? null
                              : c.testConnection,
                          child: const Text('Test connection')),
                      FilledButton.tonal(
                          onPressed: c.busy ||
                                  c.selected == null ||
                                  !c.known ||
                                  c.changed
                              ? null
                              : c.save,
                          child: const Text('Save SSH computer')),
                      if (c.busy)
                        TextButton(
                            onPressed: c.cancel, child: const Text('Cancel')),
                    ]),
                    if (c.message.isNotEmpty)
                      Padding(
                          padding: const EdgeInsets.only(top: 12),
                          child: Semantics(
                              liveRegion: true,
                              child: Text(c.message,
                                  style: TextStyle(
                                      color: c.error.isNotEmpty
                                          ? Theme.of(context).colorScheme.error
                                          : null)))),
                  ]),
            ),
          ),
          if (identity != null) _identityCard(context, c, identity),
          if (test != null) _testCard(context, test),
          if (c.hasTarget) _targetCard(context, c, status),
          if (identity != null)
            ExpansionTile(
              title: const Text('Advanced diagnostics'),
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: SelectableText(
                    'Alias: ${identity['alias']}\n'
                    'Resolved: ${identity['user']}@${identity['endpoint']}\n'
                    'Known fingerprint: ${(identity['known_fingerprints'] as List?)?.join(', ') ?? 'none'}\n'
                    'Discovered: ${(identity['discovered_fingerprints'] as List?)?.join(', ') ?? 'none'}\n'
                    'Discovery: ${identity['discovery'] ?? 'not run'}\n'
                    'Host ID: ${c.target?['target']?['host_id'] ?? 'not saved'}',
                    style:
                        const TextStyle(fontSize: 12, fontFamily: 'monospace'),
                  ),
                ),
              ],
            ),
        ]);
      });

  Widget _identityCard(
      BuildContext context, SshConnectionController c, Json identity) {
    final changed = identity['changed'] == true;
    final known = identity['known'] == true;
    final fingerprints = (identity['known_fingerprints'] as List?) ?? const [];
    final discovered =
        (identity['discovered_fingerprints'] as List?) ?? const [];
    final tone = changed ? Theme.of(context).colorScheme.error : null;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(
              changed
                  ? 'Host key changed'
                  : known
                      ? 'Host key trusted in known_hosts'
                      : 'Host key not trusted',
              style: TextStyle(fontWeight: FontWeight.w600, color: tone)),
          const SizedBox(height: 6),
          Text('${identity['user']}@${identity['endpoint']}'),
          if (fingerprints.isNotEmpty) ...[
            const SizedBox(height: 6),
            SelectableText('Trusted fingerprint: ${fingerprints.first}',
                style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
          ],
          if (discovered.isNotEmpty) ...[
            const SizedBox(height: 6),
            SelectableText(
                'Discovered by ssh-keyscan (unverified): ${discovered.first}',
                style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
          ],
          if (!known || changed) ...[
            const SizedBox(height: 8),
            Text(changed
                ? 'Odessy will not replace the trusted key. Verify the new fingerprint independently, then update your OpenSSH files deliberately.'
                : 'Verify this fingerprint independently. ssh-keyscan only discovers a key; it does not authenticate it. Add the key to known_hosts yourself, then Recheck identity. Odessy never edits trust files automatically.'),
            if (c.selected != null)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: () => c.inspect(c.selected!),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Recheck identity'),
                ),
              ),
          ],
        ]),
      ),
    );
  }

  Widget _testCard(BuildContext context, Json test) {
    final ok = test['ok'] == true;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Connection test',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          Text(ok
              ? 'SSH login verified · ${test['elapsed_ms']} ms'
              : 'SSH login failed · ${test['code'] ?? 'error'}'),
          if (!ok && '${test['message'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text('${test['message']}'),
          ],
          const SizedBox(height: 8),
          Text(ok
              ? 'Transport verified. Monitoring is read-only. Execution is not available in this build.'
              : 'Check the alias, key/agent and known host key, then test again.'),
        ]),
      ),
    );
  }

  Widget _targetCard(
      BuildContext context, SshConnectionController c, Json? status) {
    final connectivity = c.statusError.isNotEmpty
        ? 'unavailable'
        : status?['status']?['connectivity'] ?? 'unknown';
    final readiness = c.statusError.isNotEmpty
        ? 'unavailable'
        : status?['readiness'] ?? 'unavailable';
    final capabilities = status?['capabilities'] as Map?;
    final observed = status?['status']?['observed_at'];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
                child: Text('Saved SSH computer',
                    style: Theme.of(context).textTheme.titleMedium)),
            Text('$connectivity · $readiness'),
          ]),
          if (c.statusError.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(c.statusError,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 8),
          Text(observed == null
              ? 'Waiting for the first read-only status.'
              : 'Last status: $observed'),
          if ((status?['status']?['stale_groups'] as List?)?.isNotEmpty == true)
            Text(
                'Stale groups: ${(status?['status']?['stale_groups'] as List).join(', ')}',
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          const SizedBox(height: 10),
          Wrap(spacing: 18, runSpacing: 8, children: [
            _capability(
                'Transport',
                capabilities?['transport'] == true && c.statusError.isEmpty
                    ? 'verified'
                    : 'not verified'),
            _capability(
                'Monitoring',
                capabilities?['monitoring'] == true && c.statusError.isEmpty
                    ? 'read-only'
                    : 'unavailable'),
            _capability('Execution', 'not available in this build'),
            _capability('Workspace', 'not required for status'),
          ]),
          const SizedBox(height: 10),
          Wrap(spacing: 8, runSpacing: 8, children: [
            OutlinedButton.icon(
                onPressed: c.busy ? null : c.refreshStatus,
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh status')),
            TextButton(
                onPressed: c.busy
                    ? null
                    : () async {
                        final confirmed = await showDialog<bool>(
                          context: context,
                          builder: (context) => AlertDialog(
                            title: const Text('Remove SSH computer?'),
                            content: const Text(
                                'Odessy will forget this target. Your SSH key, authorized_keys and OS access remain; remove those separately if needed.'),
                            actions: [
                              TextButton(
                                  onPressed: () =>
                                      Navigator.of(context).pop(false),
                                  child: const Text('Cancel')),
                              FilledButton(
                                  onPressed: () =>
                                      Navigator.of(context).pop(true),
                                  child: const Text('Remove')),
                            ],
                          ),
                        );
                        if (confirmed == true) c.remove();
                      },
                child: const Text('Remove SSH computer')),
          ]),
          const SizedBox(height: 6),
          const Text(
              'Removing the Odessy entry does not revoke your SSH key or authorized_keys access on the host. Remove those separately if needed.'),
        ]),
      ),
    );
  }

  Widget _capability(String label, String value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(fontSize: 10, color: Colors.grey)),
          Text(value),
        ],
      );
}
