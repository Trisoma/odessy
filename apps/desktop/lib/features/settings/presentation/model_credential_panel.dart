import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../controllers/model_credential_controller.dart';

class ModelCredentialPanel extends StatefulWidget {
  const ModelCredentialPanel(
      {super.key,
      required this.backend,
      required this.provider,
      required this.endpoint,
      required this.reference,
      this.onSavingChanged});
  final ValueChanged<bool>? onSavingChanged;
  final SetupBackend backend;
  final String provider, endpoint, reference;
  @override
  State<ModelCredentialPanel> createState() => _ModelCredentialPanelState();
}

class _ModelCredentialPanelState extends State<ModelCredentialPanel> {
  final keyInput = TextEditingController();
  late ModelCredentialController controller;

  /// A key belongs to one provider, endpoint and reference. Typing in a form
  /// field must not re-read the credential status on every keystroke, so a scope
  /// change only marks the shown status stale until the key is saved or the
  /// status is refreshed explicitly.
  bool get _scopeChanged =>
      controller.provider != widget.provider ||
      controller.endpoint != widget.endpoint ||
      controller.reference != widget.reference;

  void _changed() {
    if (mounted) setState(() {});
  }

  void _create({bool read = true}) {
    controller = ModelCredentialController(widget.backend,
        provider: widget.provider,
        endpoint: widget.endpoint,
        reference: widget.reference)
      ..addListener(_changed);
    if (read) controller.load();
  }

  void _recreate({bool read = true, bool clearDraft = true}) {
    if (clearDraft) keyInput.clear();
    controller.removeListener(_changed);
    controller.dispose();
    _create(read: read);
  }

  void _refresh() {
    if (controller.busy) return;
    if (_scopeChanged) {
      _recreate(clearDraft: false);
    } else {
      controller.load();
    }
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
        oldWidget.backend != widget.backend) {
      _recreate();
      return;
    }
    if (oldWidget.endpoint != widget.endpoint ||
        oldWidget.reference != widget.reference) {
      _changed();
    }
  }

  Future<void> _save(bool persistent) async {
    final value = keyInput.text;
    keyInput.clear();
    // Save under the scope the form currently shows, not the one whose status
    // was last read.
    if (_scopeChanged) _recreate(read: false);
    widget.onSavingChanged?.call(true);
    try {
      await controller.save(value, persistent: persistent);
    } finally {
      widget.onSavingChanged?.call(false);
    }
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
            'env' => 'Configured · environment',
            'session' => 'Configured · this session only',
            _ => controller.busy
                ? 'Checking credential status…'
                : 'No API key configured',
          }),
          if (controller.busy) const LinearProgressIndicator(),
          if (_scopeChanged)
            const Text(
                'The endpoint or credential reference changed since this status was read. Saving the key applies it to the current values; refresh the status to check the new scope. Removing a key stays disabled until then, because the saved key belongs to the previous scope.',
                style: TextStyle(fontSize: 12)),
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
                // `remove` deletes the credential the controller was created
                // for. While the form names a different scope that would delete
                // the previous endpoint's key, so the status must be re-read
                // first.
                onPressed: writable &&
                        !_scopeChanged &&
                        state?.configured == true &&
                        state?.source != 'env'
                    ? controller.remove
                    : null,
                child: const Text('Remove')),
            TextButton(
                onPressed: controller.busy ? null : _refresh,
                child: const Text('Refresh key status')),
          ]),
          if (state?.message.isNotEmpty == true) Text(state!.message),
          if (state?.source == 'env')
            const Text(
                'An environment key is currently active. Saving a key here replaces it for this provider and endpoint without changing the environment.'),
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
