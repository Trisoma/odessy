import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import '../controllers/maintenance_controller.dart';

class MaintenancePage extends StatefulWidget {
  const MaintenancePage(
      {super.key,
      required this.controller,
      this.pairing = false,
      this.installer = false});
  final MaintenanceController controller;
  final bool pairing, installer;
  @override
  State<MaintenancePage> createState() => _MaintenancePageState();
}

class _MaintenancePageState extends State<MaintenancePage> {
  final editors = <String, TextEditingController>{};
  String action = 'repair';
  bool allowInference = false, allowModelDownloads = false;
  MaintenanceController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    allowInference = c.fields['allow_inference'] == true;
    allowModelDownloads = c.fields['allow_model_downloads'] == true;
    action = widget.installer
        ? 'engine:install'
        : widget.pairing
            ? 'pair:enroll'
            : 'repair';
  }

  @override
  void dispose() {
    unawaited(c.cancel());
    for (final editor in editors.values) {
      editor.dispose();
    }
    super.dispose();
  }

  void update() => c.update({
        for (final e in editors.entries) e.key: e.value.text.trim(),
        if (!widget.pairing && !widget.installer)
          'allow_inference': allowInference,
        if (!widget.pairing && !widget.installer)
          'allow_model_downloads': allowModelDownloads
      });
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
        final actions = widget.installer
            ? {
                'engine:install': 'Install using existing installer engine',
                'engine:resume': 'Resume existing installer checkpoint'
              }
            : widget.pairing
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
                      : widget.installer
                          ? 'Install Odessy'
                          : 'Runtime maintenance',
                  style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 12),
              Text(widget.pairing
                  ? 'Enrollment is not permission. Compare the certificate fingerprint out of band. Workspace grants apply only on this computer; repeat node-local consent on the execution node.'
                  : 'Local operations only. Models and external settings are preserved. Remove the recorded service before repair, upgrade, rollback or uninstall.'),
              const SizedBox(height: 18),
              if (widget.installer) ...[
                field('root', 'New installation directory', locked),
                field(
                    'cache',
                    'Download cache directory (separate from installation)',
                    locked),
                field('bundle',
                    'Verified prebuilt tool-server bundle directory', locked),
                field(
                    'config',
                    'Selection JSON (optional; default combined, no model download)',
                    locked),
                const Text(
                    'Windows x64 engine. Downloads Python/Node and pinned dependency wheels after review. Existing models stay external. Resume uses the checkpoint selection.'),
              ] else if (widget.pairing) ...[
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
                field(
                    'bundle',
                    'Bundle directory: legacy tool-server / versioned wheelhouse',
                    locked),
                field(
                    'executable',
                    'Compatible legacy setup/launcher EXE (pinned-model installs only)',
                    locked),
                CheckboxListTile(
                    title: const Text(
                        'Permit live inference during legacy pinned-model repair'),
                    value: allowInference,
                    onChanged: locked
                        ? null
                        : (value) {
                            setState(() => allowInference = value!);
                            update();
                          }),
                CheckboxListTile(
                    title: const Text(
                        'Permit downloads to repair this installation’s owned pinned model'),
                    value: allowModelDownloads,
                    onChanged: locked
                        ? null
                        : (value) {
                            setState(() => allowModelDownloads = value!);
                            update();
                          }),
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
                        : () => c.inspect(widget.installer
                            ? 'engine:status'
                            : widget.pairing
                                ? 'pair:list'
                                : 'inspect'),
                    child: Text(widget.pairing
                        ? 'Inspect enrolled peers'
                        : 'Inspect installation')),
                if (!widget.pairing && !widget.installer)
                  OutlinedButton(
                      onPressed: locked ? null : () => c.inspect('verify'),
                      child: const Text('Check integrity')),
                if (!widget.pairing && !widget.installer)
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
              if (c.result['installation_format'] != null)
                Text(
                    'Detected installation: ${c.result['installation_format']}',
                    key: const ValueKey('installation-format')),
              if (c.reviewedAction != null)
                Text('Pending operation: ${c.reviewedAction}',
                    key: const ValueKey('pending-operation')),
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
