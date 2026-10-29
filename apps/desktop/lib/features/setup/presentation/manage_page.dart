import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../controllers/installed_manage_controller.dart';
import '../../../app/setup/platform_copy.dart';

/// Installed maintenance without internal-path entry: the root, interpreter and
/// payload come from the installation record. Mutations require an exact review.
class ManagePage extends StatelessWidget {
  const ManagePage({
    super.key,
    required this.controller,
    this.external = true,
    this.onStart,
    this.onOpenMaintenance,
  });

  final InstalledManageController controller;

  /// Whether this process runs outside the installation it is managing.
  final bool external;
  final VoidCallback? onStart;
  final VoidCallback? onOpenMaintenance;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final version = c.activeVersion;
        final portable = c.spec['portable'] == true;
        return SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(28, 24, 28, 40),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Manage Odessy',
                      style: Theme.of(context).textTheme.headlineSmall),
                  const SizedBox(height: 6),
                  Text(
                      'Version ${version.isEmpty ? 'none active' : version.substring(0, 8)} · ${c.root}',
                      style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(height: 18),
                  if (c.error.isNotEmpty)
                    _message(context, c.error,
                        background: Theme.of(context).colorScheme.errorContainer,
                        onPressed: c.refreshStatus,
                        action: 'Reload status'),
                  if (c.notice.isNotEmpty)
                    _message(context, c.notice,
                        onPressed: c.dismissNotice, action: 'Dismiss'),
                  if (c.busy) const LinearProgressIndicator(),
                  const SizedBox(height: 12),
                  _summary(context, c),
                  const SizedBox(height: 20),
                  if (c.review.isNotEmpty) _review(context, c),
                  Wrap(spacing: 10, runSpacing: 10, children: [
                    OutlinedButton.icon(
                        onPressed: c.busy ? null : c.verify,
                        icon: const Icon(Icons.verified_outlined, size: 18),
                        label: const Text('Check installed files')),
                    OutlinedButton.icon(
                        onPressed: c.busy || c.uninstalled || c.payload.isEmpty
                            ? null
                            : c.reviewRepair,
                        icon: const Icon(Icons.build_outlined, size: 18),
                        label: const Text('Repair or update files')),
                    OutlinedButton.icon(
                        onPressed: c.busy || c.uninstalled || c.versions.length < 2
                            ? null
                            : () => _rollback(context, c),
                        icon: const Icon(Icons.undo, size: 18),
                        label: const Text('Roll back')),
                    OutlinedButton.icon(
                        onPressed: c.busy || c.uninstalled
                            ? null
                            : () => _configure(context, c),
                        icon: const Icon(Icons.tune, size: 18),
                        label: const Text('Change configuration')),
                    OutlinedButton.icon(
                        onPressed: c.busy || c.uninstalled || portable
                            ? null
                            : () => _services(context, c),
                        icon: const Icon(Icons.settings_power_outlined, size: 18),
                        label: const Text('Service controls')),
                    OutlinedButton.icon(
                        onPressed: c.busy || c.uninstalled || portable ? null : c.launch,
                        icon: const Icon(Icons.play_arrow_outlined, size: 18),
                        label: const Text('Start Odessy')),
                    OutlinedButton.icon(
                        onPressed: c.busy || c.uninstalled || portable ? null : c.register,
                        icon: const Icon(Icons.app_shortcut_outlined, size: 18),
                        label: Text(repairRegistrationLabel)),
                  ]),
                  const SizedBox(height: 22),
                  if (c.uninstalled)
                    Text('This installation has been removed. Close this window.',
                        style: Theme.of(context).textTheme.titleMedium)
                  else if (portable)
                    const Text('Portable build: close Odessy and remove its extracted folder. Settings, models and workspaces are separate and preserved.')
                  else if (c.status.isNotEmpty)
                    _uninstall(context, c),
                  const SizedBox(height: 18),
                  ExpansionTile(
                      tilePadding: EdgeInsets.zero,
                      title: const Text('Diagnostics'),
                      children: [
                        SelectableText(
                            const JsonEncoder.withIndent('  ')
                                .convert(c.status),
                            style: const TextStyle(fontSize: 12)),
                        if (onOpenMaintenance != null)
                          TextButton(
                              onPressed: onOpenMaintenance,
                              child: const Text(
                                  'Open the external maintenance window')),
                        if (onStart != null)
                          TextButton(
                              onPressed: onStart,
                              child: const Text('Start the local coordinator')),
                      ]),
                ]));
      });

  Widget _message(BuildContext context, String text,
          {Color? background, VoidCallback? onPressed, String? action}) =>
      Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 10),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
              color: background ??
                  Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(10)),
          child: Row(children: [
            Expanded(child: Text(text)),
            if (onPressed != null && action != null)
              TextButton(onPressed: onPressed, child: Text(action)),
          ]));

  Widget _summary(BuildContext context, InstalledManageController c) {
    final configuration = c.configuration;
    final model = configuration['model_configured'] == true
        ? 'Model configured'
        : 'Model not configured';
    final role = (configuration['role'] as String?) ?? 'unknown';
    final registration = (c.status['registration'] as String?) ?? 'none';
    final service = c.status['service_recorded'] == true
        ? 'Service recorded'
        : 'No service';
    return Wrap(spacing: 12, runSpacing: 12, children: [
      _tile(context, 'Configuration', '$role · $model'),
      _tile(context, 'Workspace',
          (configuration['workspace'] as String?) ?? 'not recorded'),
      _tile(context, 'Start menu / Installed apps', registration),
      _tile(context, 'Background service', service),
    ]);
  }

  Widget _tile(BuildContext context, String label, String value) => Container(
      width: 250,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).dividerColor),
          borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 6),
        Text(value),
      ]));

  Widget _review(BuildContext context, InstalledManageController c) {
    final review = c.review;
    final action = c.reviewedAction ?? '';
    final rows = <String>[
      'Operation: ${review['operation'] ?? action}',
      if (review['target_version'] != null)
        'Target version: ${review['target_version']}',
      if (review['workspace'] != null) 'Workspace: ${review['workspace']}',
      if (review['definition'] != null) 'Service definition: ${review['scope']}',
      if (review['keep_models'] == true) 'Existing models are preserved',
      if (review['keep_config'] == true) 'Existing configuration is preserved',
    ];
    return Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
            border: Border.all(color: Theme.of(context).colorScheme.primary),
            borderRadius: BorderRadius.circular(12)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Review before confirming',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          for (final row in rows) Text(row),
          const SizedBox(height: 12),
          Row(children: [
            FilledButton(
                onPressed: c.canConfirm ? c.confirm : null,
                child: const Text('Confirm once')),
            const SizedBox(width: 10),
            TextButton(
                onPressed: c.busy ? null : c.cancelReview,
                child: const Text('Cancel')),
          ]),
        ]));
  }

  Widget _uninstall(BuildContext context, InstalledManageController c) => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Remove Odessy', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 6),
        Text(external
            ? 'Only files recorded for this installation are removed. Models, credentials, settings and unrelated files are preserved.'
            : 'Uninstall runs from the external maintenance window so the files being removed are not in use.'),
        const SizedBox(height: 10),
        if (external)
          OutlinedButton.icon(
              onPressed: c.busy ? null : c.reviewUninstall,
              icon: const Icon(Icons.delete_outline, size: 18),
              label: const Text('Review uninstall'))
        else
          FilledButton.tonalIcon(
              onPressed: onOpenMaintenance,
              icon: const Icon(Icons.open_in_new, size: 18),
              label: const Text('Open maintenance to uninstall')),
      ]);

  Future<void> _rollback(
      BuildContext context, InstalledManageController c) async {
    final choice = await showDialog<String>(
        context: context,
        builder: (context) => SimpleDialog(
            title: const Text('Roll back to an owned version'),
            children: [
              for (final version in c.versions)
                SimpleDialogOption(
                    onPressed: () => Navigator.pop(context, version),
                    child: Text(
                        '${version.substring(0, 8)}${version == c.activeVersion ? ' · active' : ''}')),
              SimpleDialogOption(
                  onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
            ]));
    if (choice == null) return;
    await c.reviewRollback(choice);
  }

  Future<void> _services(
      BuildContext context, InstalledManageController c) async {
    await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('Background service'),
                content: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text('$serviceAccountCopy Registering one requires an existing workspace directory.'),
                  const SizedBox(height: 16),
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    OutlinedButton(
                        onPressed: () => c.service('status'),
                        child: const Text('Status')),
                    OutlinedButton(
                        onPressed: () => c.service('start'),
                        child: const Text('Start')),
                    OutlinedButton(
                        onPressed: () => c.service('stop'),
                        child: const Text('Stop')),
                    OutlinedButton(
                        onPressed: () => c.service('service-remove'),
                        child: const Text('Remove service')),
                  ]),
                ]),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('Close'))
                ]));
  }

  Future<void> _configure(
      BuildContext context, InstalledManageController c) async {
    final workspace = TextEditingController(
        text: (c.configuration['workspace'] as String?) ?? c.spec['workspace'] as String? ?? '');
    final model = TextEditingController();
    final endpoint =
        TextEditingController(text: 'http://127.0.0.1:11434');
    var provider = 'ollama';
    final submitted = await showDialog<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, setState) => AlertDialog(
                    title: const Text('Change configuration'),
                    content: SizedBox(
                        width: 460,
                        child: Column(mainAxisSize: MainAxisSize.min, children: [
                          DropdownButtonFormField<String>(
                              initialValue: provider,
                              items: const [
                                DropdownMenuItem(
                                    value: 'ollama',
                                    child: Text('Existing Ollama model')),
                                DropdownMenuItem(
                                    value: 'openai',
                                    child: Text('Compatible remote endpoint')),
                                DropdownMenuItem(
                                    value: 'later',
                                    child: Text('Configure later')),
                              ],
                              onChanged: (value) =>
                                  setState(() => provider = value ?? 'ollama'),
                              decoration: const InputDecoration(
                                  labelText: 'Model provider')),
                          if (provider != 'later') ...[
                            TextField(
                                controller: model,
                                decoration: const InputDecoration(
                                    labelText: 'Existing model name')),
                            TextField(
                                controller: endpoint,
                                decoration: const InputDecoration(
                                    labelText: 'Model endpoint')),
                          ],
                          TextField(
                              controller: workspace,
                              decoration: const InputDecoration(
                                  labelText:
                                      'Workspace directory for local tools')),
                          const SizedBox(height: 8),
                          const Text(
                              'Nothing is downloaded or started. No credential is stored.',
                              style: TextStyle(fontSize: 12)),
                        ])),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(context, false),
                          child: const Text('Cancel')),
                      FilledButton(
                          onPressed: () => Navigator.pop(context, true),
                          child: const Text('Review change')),
                    ])));
    final selection = <String, dynamic>{
      'role': (c.configuration['role'] as String?) ?? 'combined',
      if (provider != 'later')
        'model': {
          'provider': provider,
          'name': model.text.trim(),
          if (endpoint.text.trim().isNotEmpty) 'endpoint': endpoint.text.trim(),
        },
    };
    final targetWorkspace = workspace.text.trim();
    workspace.dispose();
    model.dispose();
    endpoint.dispose();
    if (submitted != true) return;
    await c.reviewConfiguration(selection, targetWorkspace);
  }
}

/// Convenience for the installed application: the recorded workspace is the
/// only place a first-run configuration needs to be confirmed.
Json workspaceSelection(Json configuration, String workspace) => {
      'role': configuration['role'] ?? 'combined',
      'workspace': workspace,
    };
