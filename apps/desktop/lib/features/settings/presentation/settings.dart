import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.controller});
  final DesktopSession controller;
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final count = TextEditingController();
  Json? original;
  String status = '';
  bool loading = true;
  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    count.dispose();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final value = await widget.controller.loadSettings();
      if (!mounted) return;
      setState(() {
        original = value;
        count.text =
            '${(value['settings'] as Map?)?['max_consultations'] ?? 1}';
        status = '';
        loading = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          loading = false;
          status =
              'Could not load settings. Connect the local service and retry.';
        });
      }
    }
  }

  Future<void> save() async {
    final value = int.tryParse(count.text);
    if (value == null || value < 0 || value > 32) {
      setState(() => status = 'Enter a whole number from 0 to 32.');
      return;
    }
    setState(() => loading = true);
    try {
      // Refresh before the read-modify-write; do not overwrite unrelated stale fields.
      // The current backend has no revision/CAS API, so concurrent writes remain a
      // documented preview limitation rather than a fabricated revision guarantee.
      await widget.controller.updateConsultationLimit(value);
      if (!mounted) return;
      await load();
      if (mounted) {
        setState(() => status = widget.controller.api.isDemo
            ? 'Saved in demo memory only.'
            : 'Saved by the service. Applies to newly created sessions.');
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          status =
              'Save failed. No success assumed; reload before trying again.';
          loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) =>
      ListView(padding: const EdgeInsets.all(32), children: [
        Text('Keep it yours.', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 8),
        const Text('A small, real settings seam for the desktop foundation.',
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
                          style: TextStyle(fontSize: 11, color: Colors.grey)),
                      const SizedBox(height: 20),
                      SizedBox(
                          width: 240,
                          child: TextField(
                              key: const Key('consultation-limit'),
                              controller: count,
                              enabled: !loading && original != null,
                              keyboardType: TextInputType.number,
                              decoration: const InputDecoration(
                                  labelText: 'Limit · 0–32'))),
                      const SizedBox(height: 18),
                      Wrap(spacing: 10, children: [
                        FilledButton(
                            onPressed:
                                loading || original == null ? null : save,
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
                          style: TextStyle(fontSize: 11, color: Colors.grey)),
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
                          style: TextStyle(color: Colors.grey, fontSize: 12)),
                      SizedBox(height: 10),
                      Text(
                          'Connection tokens stay in memory and are never written to a config file.',
                          style: TextStyle(color: Colors.grey, fontSize: 12))
                    ]))),
      ]);
}
