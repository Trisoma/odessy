import 'dart:async';
import 'package:flutter/material.dart';
import '../../../app/setup/native_picker.dart';
import '../../setup/controllers/maintenance_controller.dart';

/// Presentation over existing exact-review Rust operations; no trust edits here.
class PairingPage extends StatefulWidget {
  const PairingPage(
      {super.key,
      required this.controller,
      this.picker = const UnavailablePicker()});
  final MaintenanceController controller;
  final NativePicker picker;
  @override
  State<PairingPage> createState() => _PairingPageState();
}

class _PairingPageState extends State<PairingPage> {
  final editors = <String, TextEditingController>{};
  String action = 'enroll', capability = 'mcp';
  bool compared = false;
  MaintenanceController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) c.inspect('pair:list');
    });
  }

  @override
  void dispose() {
    unawaited(c.cancel());
    for (final e in editors.values) {
      e.dispose();
    }
    super.dispose();
  }

  void update() {
    compared = false;
    c.update({
      if (!editors.containsKey('link') && c.fields['link'] != null)
        'link': c.fields['link'],
      for (final e in editors.entries) e.key: e.value.text.trim(),
      'capability': capability
    });
  }

  Widget field(String key, String label, {String? browse}) {
    final editor = editors.putIfAbsent(
        key, () => TextEditingController(text: c.fields[key] as String? ?? ''));
    return Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: TextField(
            controller: editor,
            key: ValueKey('pair-$key'),
            enabled: !c.busy && !c.needsInspection,
            maxLines: 1,
            decoration: InputDecoration(
                labelText: label,
                suffixIcon: browse == null
                    ? null
                    : IconButton(
                        tooltip: 'Browse $label',
                        icon: const Icon(Icons.folder_open),
                        onPressed: c.busy
                            ? null
                            : () async {
                                final path = browse == 'folder'
                                    ? await widget.picker.pickDirectory()
                                    : browse == 'save'
                                        ? await widget.picker.pickSaveLocation(
                                            suggestedName:
                                                'odessy-identity.json')
                                        : await widget.picker.pickFile();
                                if (path != null && mounted) {
                                  editor.text = path;
                                  setState(update);
                                }
                              })),
            onChanged: (_) => setState(update)));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) =>
          ListView(padding: const EdgeInsets.all(28), children: [
            Text('Connect a device',
                style: Theme.of(context).textTheme.headlineSmall),
            const Text(
                'Pairing establishes identity. It does not grant execution or monitoring. Permissions below apply on this computer; the other device must grant its own permissions.'),
            const SizedBox(height: 16),
            ExpansionTile(title: const Text('Connection component'), children: [
              field('link', 'odessy-link executable · automatic when available',
                  browse: 'file')
            ]),
            Wrap(spacing: 12, runSpacing: 8, children: [
              OutlinedButton(
                  onPressed: c.busy ? null : () => c.inspect('pair:list'),
                  child: const Text('Refresh paired devices')),
              OutlinedButton(
                  onPressed: c.busy || c.needsInspection
                      ? null
                      : () => c.inspect('pair:discover'),
                  child: const Text('Find nearby devices · unverified')),
            ]),
            const SizedBox(height: 16),
            if (c.result['peers'] is Map) ...[
              Text(
                  'Local trust revision: ${(c.result['peers'] as Map)['grant_revision']}'),
              for (final peer
                  in ((c.result['peers'] as Map)['peers'] as List? ?? []))
                Card(
                    child: ListTile(
                        title: Text('${peer['alias']}'),
                        subtitle: SelectableText(
                            'Identity: ${peer['installation_id']}\nFingerprint: ${peer['fingerprint_sha256']}\nTrust: ${peer['revoked'] == true ? 'Revoked' : peer['revoked'] == false ? 'Paired' : 'Not reported by this component'}\nExecution: ${peer['capability_mcp'] == true ? 'granted' : 'not granted'} · Monitoring: ${peer['capability_telemetry'] == true ? 'granted' : 'not granted'}\nWorkspace: ${peer['workspace'] ?? 'none'}\nConnection: unknown · enrollment is not a health check'),
                        trailing: TextButton(
                            onPressed: c.busy
                                ? null
                                : () {
                                    final matches = c.peers?.where(
                                        (candidate) =>
                                            candidate.alias == peer['alias']);
                                    if (matches == null ||
                                        matches.length != 1) {
                                      return;
                                    }
                                    final e = editors.putIfAbsent(
                                        'alias', () => TextEditingController());
                                    e.text = '${peer['alias']}';
                                    c.selectPeer(matches.single);
                                    setState(() {
                                      action = 'revoke';
                                      update();
                                    });
                                  },
                            child: const Text('Review revocation')))),
            ],
            const Divider(height: 32),
            DropdownButtonFormField<String>(
                initialValue: action,
                key: ValueKey(action),
                isExpanded: true,
                decoration:
                    const InputDecoration(labelText: 'Device operation'),
                items: [
                  for (final entry in {
                    'init': 'Create this computer’s identity',
                    'export': 'Export public identity',
                    'enroll': 'Enroll a verified identity',
                    'grant': 'Grant a local capability',
                    'revoke': 'Revoke all local capabilities'
                  }.entries)
                    DropdownMenuItem(value: entry.key, child: Text(entry.value))
                ],
                onChanged: c.busy || c.needsInspection
                    ? null
                    : (value) => setState(() {
                          action = value!;
                          update();
                        })),
            const SizedBox(height: 16),
            if (['enroll', 'grant', 'revoke'].contains(action))
              field('alias', 'Device alias'),
            if (action == 'export')
              field('output', 'New public identity file', browse: 'save'),
            if (action == 'enroll') ...[
              field('certificate', 'Peer public identity file', browse: 'file'),
              field('fingerprint', 'Verified SHA-256 fingerprint'),
              field('address', 'Address · hostname:port or [IPv6]:port'),
              CheckboxListTile(
                  value: compared,
                  onChanged: c.busy
                      ? null
                      : (value) => setState(() => compared = value!),
                  title: const Text(
                      'I compared the fingerprint over an independent trusted channel')),
            ],
            if (action == 'grant') ...[
              DropdownButtonFormField<String>(
                  initialValue: capability,
                  decoration: const InputDecoration(labelText: 'Capability'),
                  items: const [
                    DropdownMenuItem(
                        value: 'mcp', child: Text('Workspace tool transport')),
                    DropdownMenuItem(
                        value: 'telemetry.read',
                        child: Text('Read-only monitoring'))
                  ],
                  onChanged: c.busy
                      ? null
                      : (value) => setState(() {
                            capability = value!;
                            update();
                          })),
              if (capability == 'mcp')
                field('workspace', 'Existing local workspace',
                    browse: 'folder'),
              const Text(
                  'Transport permission never bypasses Python policy or action approval.'),
            ],
            if (action == 'revoke')
              const Text(
                  'Revocation blocks new admissions and initiates session cleanup. Completed actions are not undone. The other device’s registry is independent.'),
            Wrap(spacing: 12, runSpacing: 8, children: [
              OutlinedButton(
                  onPressed: c.busy ||
                          c.needsInspection ||
                          (action == 'enroll' && !compared)
                      ? null
                      : () => c.review('pair:$action'),
                  child: const Text('Review operation')),
              FilledButton(
                  onPressed: c.canApply
                      ? () async {
                          await c.apply();
                          if (mounted && c.needsInspection && c.error.isEmpty) {
                            await c.inspect('pair:list');
                          }
                        }
                      : null,
                  child: const Text('Confirm reviewed operation')),
              if (c.busy)
                TextButton(onPressed: c.cancel, child: const Text('Cancel')),
            ]),
            if (c.reviewedAction != null) ...[
              const SizedBox(height: 12),
              Text('Reviewed operation: ${c.result['operation']}'),
              SelectableText((c.result['arguments'] as List? ?? []).join('\n')),
              const Text(
                  'Confirmation applies once to these exact arguments, executable, identity file and local trust revision.')
            ],
            if (c.busy)
              const LinearProgressIndicator(
                  semanticsLabel: 'Device operation in progress'),
            Text(c.status),
            if (c.error.isNotEmpty)
              Text(c.error,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            if (c.result['message'] is String &&
                (c.result['message'] as String).isNotEmpty)
              SelectableText(c.result['message'] as String),
          ]));
}
