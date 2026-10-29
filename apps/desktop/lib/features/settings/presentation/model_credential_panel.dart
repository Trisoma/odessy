import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../controllers/model_credential_controller.dart';

class ModelCredentialPanel extends StatefulWidget {
  const ModelCredentialPanel(
      {super.key,
      required this.backend,
      required this.provider,
      required this.endpoint,
      required this.reference});
  final SetupBackend backend;
  final String provider, endpoint, reference;
  @override
  State<ModelCredentialPanel> createState() => _ModelCredentialPanelState();
}

class _ModelCredentialPanelState extends State<ModelCredentialPanel> {
  final keyInput = TextEditingController();
  late ModelCredentialController controller;
  void _changed() {
    if (mounted) setState(() {});
  }

  void _create() {
    controller = ModelCredentialController(widget.backend,
        provider: widget.provider,
        endpoint: widget.endpoint,
        reference: widget.reference)
      ..addListener(_changed);
    controller.load();
  }

  @override
  void initState() {
    super.initState();
    _create();
  }

  @override
  void didUpdateWidget(covariant ModelCredentialPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.provider != widget.provider ||
        oldWidget.endpoint != widget.endpoint ||
        oldWidget.reference != widget.reference ||
        oldWidget.backend != widget.backend) {
      keyInput.clear();
      controller.removeListener(_changed);
      controller.dispose();
      _create();
    }
  }

  Future<void> _save(bool persistent) async {
    final value = keyInput.text;
    keyInput.clear();
    await controller.save(value, persistent: persistent);
  }

  @override
  void dispose() {
    keyInput.clear();
    keyInput.dispose();
    controller.removeListener(_changed);
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = controller.current;
    final writable = !controller.busy && state?.writable == true;
    return Padding(
        padding: const EdgeInsets.symmetric(vertical: 16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('API key', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(switch (state?.source) {
            'keyring' => 'Configured · OS credential store',
            'env' => 'Configured · environment (read-only)',
            'session' => 'Configured · this session only',
            _ => controller.busy
                ? 'Checking credential status…'
                : 'No API key configured',
          }),
          if (controller.busy) const LinearProgressIndicator(),
          const SizedBox(height: 12),
          TextField(
              key: const ValueKey('model-api-key'),
              controller: keyInput,
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
              enabled: writable,
              decoration: const InputDecoration(
                  labelText: 'Enter or replace API key',
                  helperText:
                      'Saved separately from model settings. Existing keys are never displayed.')),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            FilledButton(
                onPressed: writable && state?.secureStorageAvailable == true
                    ? () => _save(true)
                    : null,
                child: const Text('Save securely')),
            OutlinedButton(
                onPressed: writable ? () => _save(false) : null,
                child: const Text('Use for this session only')),
            TextButton(
                onPressed: writable && state?.configured == true
                    ? controller.remove
                    : null,
                child: const Text('Remove')),
            TextButton(
                onPressed: controller.busy ? null : controller.load,
                child: const Text('Refresh key status')),
          ]),
          if (state?.message.isNotEmpty == true) Text(state!.message),
          if (state?.source == 'env')
            const Text(
                'An environment value takes precedence. Remove it and reopen Odessy to manage this key here.'),
          if (controller.message.isNotEmpty)
            Semantics(liveRegion: true, child: Text(controller.message)),
          if (controller.error.isNotEmpty)
            Semantics(
                liveRegion: true,
                child: Text(controller.error,
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error))),
        ]));
  }
}
