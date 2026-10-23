import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import '../controllers/maintenance_controller.dart';

class MaintenancePage extends StatefulWidget {
  const MaintenancePage(
      {super.key, required this.controller, this.pairing = false});
  final MaintenanceController controller;
  final bool pairing;
  @override
  State<MaintenancePage> createState() => _MaintenancePageState();
}

class _MaintenancePageState extends State<MaintenancePage> {
  final editors = <String, TextEditingController>{};
  String action = 'repair';
  MaintenanceController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    action = widget.pairing ? 'pair:enroll' : 'repair';
  }

  @override
  void dispose() {
    unawaited(c.cancel());
    for (final editor in editors.values) {
      editor.dispose();
    }
    super.dispose();
  }

  void update() =>
      c.update({for (final e in editors.entries) e.key: e.value.text.trim()});
  Widget field(String key, String label, bool locked) {
    final editor = editors.putIfAbsent(
        key, () => TextEditingController(text: c.fields[key] as String? ?? ''));
    return Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextField(
            key: ValueKey('native-$key'),
            controller: editor,
            enabled: !locked,
            decoration: InputDecoration(labelText: label),
            onChanged: (_) => update()));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final locked = c.busy || c.needsInspection;
        final actions = widget.pairing
            ? {
                'pair:init': 'Initialize this computer',
                'pair:export': 'Export public identity',
                'pair:enroll': 'Enroll verified peer',
                'pair:grant': 'Grant this local workspace',
                'pair:revoke': 'Revoke local peer grant'
              }
            : {
                'install': 'Install / upgrade offline runtime',
                'repair': 'Repair into a new version',
                'rollback': 'Rollback owned version',
                'service-install': 'Register user service',
                'start': 'Start user service',
                'stop': 'Stop user service',
                'service-remove': 'Remove user service',
                'uninstall': 'Uninstall matching owned files'
              };
        return SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(
                  widget.pairing
                      ? 'Verified device pairing'
                      : 'Runtime maintenance',
                  style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 12),
              Text(widget.pairing
                  ? 'Enrollment is not permission. Compare the certificate fingerprint out of band. Workspace grants apply only on this computer; repeat node-local consent on the execution node.'
                  : 'Local operations only. Models and external settings are preserved. Remove the recorded service before repair, upgrade, rollback or uninstall.'),
              const SizedBox(height: 18),
              if (widget.pairing) ...[
                field('link', 'Existing odessy-link executable', locked),
                field('alias', 'Peer alias', locked),
                field('certificate', 'Peer public identity bundle', locked),
                field('fingerprint',
                    'Independently verified SHA-256 fingerprint', locked),
                field('address', 'Peer address (optional)', locked),
                field('workspace', 'Local workspace for an explicit grant',
                    locked),
                field('output', 'New public identity export path', locked),
              ] else ...[
                field('root', 'Owned installation directory', locked),
                field('bundle', 'Offline wheelhouse directory', locked),
                field(
                    'config',
                    'Selection JSON (optional; otherwise combined role)',
                    locked),
                field('workspace', 'Existing execution workspace', locked),
                field('version', 'Owned rollback version ID', locked),
                field('node_link', 'Existing odessy-link (execution nodes)',
                    locked),
                field('node_server',
                    'Existing Node server entry (execution nodes)', locked),
              ],
              DropdownButtonFormField<String>(
                  isExpanded: true,
                  initialValue: action,
                  items: actions.entries
                      .map((e) =>
                          DropdownMenuItem(value: e.key, child: Text(e.value)))
                      .toList(),
                  onChanged: locked
                      ? null
                      : (value) {
                          setState(() => action = value!);
                          update();
                        },
                  decoration:
                      const InputDecoration(labelText: 'Explicit operation')),
              const SizedBox(height: 16),
              Wrap(spacing: 10, runSpacing: 10, children: [
                OutlinedButton(
                    onPressed: c.busy
                        ? null
                        : () =>
                            c.inspect(widget.pairing ? 'pair:list' : 'inspect'),
                    child: Text(widget.pairing
                        ? 'Inspect enrolled peers'
                        : 'Inspect installation')),
                if (!widget.pairing)
                  OutlinedButton(
                      onPressed: locked ? null : () => c.inspect('verify'),
                      child: const Text('Check integrity')),
                if (!widget.pairing)
                  OutlinedButton(
                      onPressed: locked ? null : () => c.inspect('status'),
                      child: const Text('Service status')),
                if (widget.pairing)
                  OutlinedButton(
                      onPressed:
                          locked ? null : () => c.inspect('pair:discover'),
                      child: const Text('Discover addresses')),
                OutlinedButton(
                    onPressed: locked ? null : () => c.review(action),
                    child: const Text('Review operation')),
                FilledButton(
                    onPressed: c.canApply ? c.apply : null,
                    child: const Text('Confirm reviewed operation')),
                if (c.busy)
                  TextButton(
                      onPressed: c.cancel,
                      child: const Text('Cancel operation')),
              ]),
              const SizedBox(height: 16),
              if (c.busy) const LinearProgressIndicator(),
              Text(c.status),
              if (c.error.isNotEmpty)
                Text(c.error,
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error)),
              if (c.result.isNotEmpty)
                SelectableText(
                    const JsonEncoder.withIndent('  ').convert(c.result)),
            ]));
      });
}
