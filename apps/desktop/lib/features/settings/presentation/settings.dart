import 'package:flutter/material.dart';
import '../controllers/settings_form_controller.dart';
import '../../../app/session_controller.dart';
import '../controllers/setting_field.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage(
      {super.key, required this.controller, this.onOpenManagement});
  final DesktopSession controller;
  final VoidCallback? onOpenManagement;
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final count = TextEditingController();
  final field = SettingField<int>(
      source: 'user collaboration configuration',
      applyTiming: 'new sessions',
      parse: int.tryParse,
      validate: (v) =>
          v < 0 || v > 32 ? 'Enter a whole number from 0 to 32.' : null);
  late final SettingsFormController<int> form;
  bool get loading => form.loading;
  String get status => form.error ?? form.status;
  @override
  void initState() {
    super.initState();
    form = SettingsFormController(
        field: field,
        read: () async {
          final transport = widget.controller.api;
          final value = await widget.controller.loadSettings();
          if (!identical(transport, widget.controller.api)) {
            throw StateError('Connection changed');
          }
          return (value['settings'] as Map)['max_consultations'] as int;
        },
        write: widget.controller.updateConsultationLimit);
    form.addListener(syncText);
    form.load();
  }

  void syncText() {
    if (count.text != field.text) count.text = field.text;
  }

  Future<void> load() => form.load();
  Future<void> save() => form.save(
      successMessage: widget.controller.api.isDemo
          ? 'Saved in demo memory only.'
          : 'Saved by the service. Applies to newly created sessions.');
  @override
  void dispose() {
    form.removeListener(syncText);
    form.dispose();
    count.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: form,
      builder: (context, _) =>
          ListView(padding: const EdgeInsets.all(32), children: [
            Text('Keep it yours.',
                style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            const Text(
                'A small, real settings seam for the desktop foundation.',
                style: TextStyle(color: Colors.grey)),
            const SizedBox(height: 28),
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('Collaboration',
                              style: TextStyle(
                                  fontSize: 16, fontWeight: FontWeight.w600)),
                          const SizedBox(height: 10),
                          const Text('Maximum consultations per turn',
                              style: TextStyle(fontSize: 13)),
                          const SizedBox(height: 8),
                          const Text(
                              'Source: user collaboration configuration via /v1/settings. Other fields and participant profiles are preserved. This does not enable consultation.',
                              style:
                                  TextStyle(fontSize: 11, color: Colors.grey)),
                          const SizedBox(height: 20),
                          SizedBox(
                              width: 240,
                              child: TextField(
                                  key: const Key('consultation-limit'),
                                  controller: count,
                                  onChanged: (value) => form.edit(value),
                                  enabled: !loading && form.loaded,
                                  keyboardType: TextInputType.number,
                                  decoration: const InputDecoration(
                                      labelText: 'Limit · 0–32'))),
                          const SizedBox(height: 18),
                          Wrap(spacing: 10, children: [
                            FilledButton(
                                onPressed: form.canSave ? save : null,
                                child: const Text('Save settings')),
                            OutlinedButton(
                                onPressed: loading ? null : load,
                                child: const Text('Reload'))
                          ]),
                          if (loading)
                            const Padding(
                                padding: EdgeInsets.only(top: 16),
                                child: LinearProgressIndicator()),
                          if (status.isNotEmpty)
                            Padding(
                                padding: const EdgeInsets.only(top: 16),
                                child: Text(status,
                                    style: const TextStyle(fontSize: 12))),
                          const SizedBox(height: 16),
                          const Text(
                              'Apply timing: new sessions. Existing active turns retain their configuration.',
                              style:
                                  TextStyle(fontSize: 11, color: Colors.grey)),
                        ]))),
            const SizedBox(height: 20),
            if (widget.onOpenManagement != null)
              Card(
                  child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('Installation',
                                style: TextStyle(fontWeight: FontWeight.w600)),
                            const SizedBox(height: 10),
                            const Text(
                                'Repair, update, roll back, change configuration or uninstall this installed copy. Maintenance runs outside the installation.',
                                style: TextStyle(color: Colors.grey, fontSize: 12)),
                            const SizedBox(height: 12),
                            OutlinedButton.icon(
                                onPressed: widget.onOpenManagement,
                                icon: const Icon(Icons.settings_outlined,
                                    size: 18),
                                label:
                                    const Text('Manage installation')),
                          ]))),
            const SizedBox(height: 20),
            const Card(
                child: Padding(
                    padding: EdgeInsets.all(24),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('More settings are coming',
                              style: TextStyle(fontWeight: FontWeight.w600)),
                          SizedBox(height: 10),
                          Text(
                              'Model/provider editing, audio devices, MCP servers, usage, jobs and host access need their dedicated management contracts. They remain configured through the existing CLI and user configuration.',
                              style:
                                  TextStyle(color: Colors.grey, fontSize: 12)),
                          SizedBox(height: 10),
                          Text(
                              'Connection tokens stay in memory and are never written to a config file.',
                              style:
                                  TextStyle(color: Colors.grey, fontSize: 12))
                        ]))),
          ]));
}
