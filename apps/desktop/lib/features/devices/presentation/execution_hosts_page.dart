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
              'Connect with SSH, then explicitly select it for agent commands. OdessyExecutor LAN remains experimental.'),
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

class _SshSection extends StatefulWidget {
  const _SshSection({required this.controller});
  final SshConnectionController controller;
  @override
  State<_SshSection> createState() => _SshSectionState();
}

class _SshSectionState extends State<_SshSection> {
  final targetInput = TextEditingController();
  final portInput = TextEditingController();
  final passwordInput = TextEditingController();
  bool obscurePassword = true;
  bool initialized = false;
  SshConnectionApi? inputService;
  String validation = '';
  SshConnectionController get controller => widget.controller;

  @override
  void dispose() {
    targetInput.dispose();
    portInput.dispose();
    passwordInput.clear();
    passwordInput.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final address = targetInput.text.trim();
    final rawPort = portInput.text.trim();
    final port = rawPort.isEmpty ? null : int.tryParse(rawPort);
    if (address.isEmpty ||
        (rawPort.isNotEmpty && (port == null || port < 1 || port > 65535))) {
      setState(() => validation = address.isEmpty
          ? 'Enter user@hostname, for example jaimef@10.1.1.2.'
          : 'Port must be a number between 1 and 65535.');
      return;
    }
    setState(() => validation = '');
    var password = passwordInput.text;
    passwordInput.clear();
    try {
      await controller.connect(address, port: port, password: password);
    } finally {
      password = '';
    }
  }

  void _edited() {
    initialized = true;
    validation = '';
    controller.editTarget(targetInput.text);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        if (!identical(inputService, c.api)) {
          inputService = c.api;
          targetInput.clear();
          portInput.clear();
          passwordInput.clear();
          initialized = false;
          validation = '';
        }
        if (c.api == null) {
          return const Card(
              child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('Connect the local Odessy service to use SSH.')));
        }
        if (!initialized && c.hasTarget) {
          initialized = true;
          targetInput.text = '${c.target?['target']?['alias'] ?? ''}';
          final port = c.target?['target']?['port'];
          portInput.text = port is int ? '$port' : '';
        }
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Card(
              child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(children: [
                          const Icon(Icons.terminal_outlined),
                          const SizedBox(width: 10),
                          Text('SSH connection',
                              style: Theme.of(context).textTheme.titleMedium)
                        ]),
                        const SizedBox(height: 16),
                        TextField(
                            key: const Key('ssh-target'),
                            controller: targetInput,
                            enabled: !c.busy,
                            autocorrect: false,
                            enableSuggestions: false,
                            decoration: const InputDecoration(
                                labelText: 'Address',
                                hintText: 'user@hostname',
                                helperText: 'Example: jaimef@10.1.1.2'),
                            onChanged: (_) => _edited(),
                            onSubmitted: (_) => _connect()),
                        const SizedBox(height: 12),
                        ConstrainedBox(
                            constraints: const BoxConstraints(maxWidth: 180),
                            child: TextField(
                                key: const Key('ssh-port'),
                                controller: portInput,
                                enabled: !c.busy,
                                keyboardType: TextInputType.number,
                                decoration: const InputDecoration(
                                    labelText: 'Port (optional)',
                                    hintText: '22 or your SSH config'),
                                onChanged: (_) => _edited(),
                                onSubmitted: (_) => _connect())),
                        const SizedBox(height: 12),
                        TextField(
                            key: const Key('ssh-password'),
                            controller: passwordInput,
                            enabled: !c.busy,
                            obscureText: obscurePassword,
                            enableSuggestions: false,
                            autocorrect: false,
                            decoration: InputDecoration(
                                labelText: 'Password (optional)',
                                helperText:
                                    'Leave blank to use your SSH key or agent.',
                                suffixIcon: IconButton(
                                    tooltip: obscurePassword
                                        ? 'Show password'
                                        : 'Hide password',
                                    onPressed: () => setState(() =>
                                        obscurePassword = !obscurePassword),
                                    icon: Icon(obscurePassword
                                        ? Icons.visibility_outlined
                                        : Icons.visibility_off_outlined))),
                            onSubmitted: (_) => _connect()),
                        const SizedBox(height: 16),
                        Wrap(
                            spacing: 10,
                            runSpacing: 8,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              FilledButton.icon(
                                  key: const Key('ssh-connect'),
                                  onPressed: c.busy ? null : _connect,
                                  icon: const Icon(Icons.link),
                                  label: Text(
                                      c.hasTarget ? 'Reconnect' : 'Connect')),
                              if (c.hasTarget)
                                OutlinedButton(
                                    onPressed: c.busy ? null : c.remove,
                                    child: const Text('Disconnect')),
                              if (c.busy)
                                TextButton(
                                    onPressed: c.cancel,
                                    child: const Text('Stop waiting'))
                            ]),
                        const SizedBox(height: 12),
                        const Text(
                            'Temporary connection for this Odessy service session. Passwords are not saved. Reconnect after restarting.'),
                        if (c.busy)
                          const Padding(
                              padding: EdgeInsets.only(top: 12),
                              child: LinearProgressIndicator()),
                        if (validation.isNotEmpty || c.message.isNotEmpty)
                          Padding(
                              padding: const EdgeInsets.only(top: 12),
                              child: Semantics(
                                  liveRegion: true,
                                  child: Text(
                                      validation.isNotEmpty
                                          ? validation
                                          : c.message,
                                      style: TextStyle(
                                          color: validation.isNotEmpty ||
                                                  c.error.isNotEmpty
                                              ? Theme.of(context)
                                                  .colorScheme
                                                  .error
                                              : null)))),
                      ]))),
          if (c.test != null) _testCard(context, c.test!),
          if (c.hasTarget) _targetCard(context, c, c.status),
          if (c.hasTarget && c.api is SshExecutionApi)
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                              c.execution == null
                                  ? 'Remote agent commands'
                                  : 'SSH command target: ${c.execution!['target']}',
                              style: Theme.of(context).textTheme.titleMedium),
                          const SizedBox(height: 8),
                          const Text(
                              'Custom engine preview · POSIX shell commands run as the SSH user WITHOUT a remote sandbox. Every exact command needs approval. Stop closes the SSH channel; detached remote processes may survive. No remote file inspector or interactive terminal.'),
                          const SizedBox(height: 8),
                          FilledButton.icon(
                              key: const Key('ssh-execution'),
                              onPressed: c.busy
                                  ? null
                                  : () => c.setExecution(c.execution == null),
                              icon: const Icon(Icons.terminal),
                              label: Text(c.execution == null
                                  ? 'Use for agent commands'
                                  : 'Disconnect agent commands')),
                        ]))),
          ExpansionTile(
              title: const Text('Advanced: SSH aliases and host identity'),
              children: [
                Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                              'Use an existing OpenSSH shortcut instead of an address. Selecting one fills the same Address field.'),
                          if (c.aliases.isNotEmpty)
                            DropdownButtonFormField<String>(
                                key: ValueKey('ssh-alias-${c.selected}'),
                                initialValue: c.aliases.contains(c.selected)
                                    ? c.selected
                                    : null,
                                decoration: const InputDecoration(
                                    labelText: 'SSH alias'),
                                items: [
                                  for (final alias in c.aliases)
                                    DropdownMenuItem(
                                        value: alias, child: Text(alias))
                                ],
                                onChanged: c.busy
                                    ? null
                                    : (value) {
                                        if (value != null) {
                                          targetInput.text = value;
                                          portInput.clear();
                                          passwordInput.clear();
                                          _edited();
                                        }
                                      }),
                          const SizedBox(height: 10),
                          OutlinedButton.icon(
                              onPressed: c.busy ? null : c.refreshAliases,
                              icon: const Icon(Icons.refresh),
                              label: const Text('Refresh aliases')),
                          if (c.identity != null)
                            _identityCard(context, c, c.identity!),
                          if (c.identity != null)
                            SelectableText(
                                'Connection target: ${c.identity!['alias']}\n'
                                'Resolved: ${c.identity!['user']}@${c.identity!['endpoint']}\n'
                                'Trusted fingerprint: ${_fingerprints(c.identity!['known_fingerprints'])}',
                                style: const TextStyle(
                                    fontSize: 12, fontFamily: 'monospace')),
                        ]))
              ]),
        ]);
      });
  String _fingerprints(Object? value) =>
      value is List && value.isNotEmpty ? value.join(', ') : 'none';

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
            const Text(
                'After resolving trust, choose Connect again using the address and port above.'),
          ],
        ]),
      ),
    );
  }

  Widget _testCard(BuildContext context, Json test) {
    final ok = test['ok'] == true;
    final gitOnly =
        test['authenticated'] == true && test['code'] == 'SSH_NO_SHELL';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Connection test',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          Text(ok
              ? 'SSH login verified · ${test['elapsed_ms']} ms'
              : gitOnly
                  ? 'Authenticated · Git-only endpoint'
                  : 'SSH login failed · ${test['code'] ?? 'error'}'),
          const SizedBox(height: 8),
          Text(ok
              ? 'Login verified. Monitoring is separate from the optional agent command target below.'
              : '${test['message'] ?? 'Check the username, password / SSH agent and trusted host key.'}'),
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
                child: Text(
                    c.target?['target']?['temporary'] == true
                        ? 'Session connection'
                        : 'Saved SSH computer',
                    style: Theme.of(context).textTheme.titleMedium)),
            Text('$connectivity · $readiness'),
          ]),
          if (c.statusError.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(c.statusError,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 8),
          Text('Connected target: ${c.target?['target']?['alias'] ?? ''}'
              '${c.target?['target']?['port'] == null ? '' : ':${c.target?['target']?['port']}'}'),
          if (c.target?['target']?['password_required'] == true) ...[
            const SizedBox(height: 8),
            const Text(
                'Enter the password above and choose Reconnect. Passwords are never saved.'),
          ],
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
            _capability(
                'Agent commands',
                c.execution == null
                    ? 'not selected'
                    : 'exact approval required'),
            _capability('Workspace', 'not required for status'),
          ]),
          const SizedBox(height: 10),
          Wrap(spacing: 8, runSpacing: 8, children: [
            OutlinedButton.icon(
                onPressed: c.busy ? null : c.refreshStatus,
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh status')),
            if (c.target?['target']?['temporary'] != true)
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
