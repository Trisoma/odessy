import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';
import '../controllers/pairing_controller.dart';

class ShortCodePanel extends StatefulWidget {
  const ShortCodePanel(
      {super.key, required this.backend, required this.address});
  final PairingBackend backend;
  final String address;
  @override
  State<ShortCodePanel> createState() => _ShortCodePanelState();
}

class _ShortCodePanelState extends State<ShortCodePanel> {
  late final c = PairingController(widget.backend);
  final code = TextEditingController();
  @override
  void initState() {
    super.initState();
    unawaited(c.load());
  }

  @override
  void didUpdateWidget(covariant ShortCodePanel old) {
    super.didUpdateWidget(old);
    if (old.address != widget.address) {
      code.clear();
      unawaited(_reset());
    }
  }

  Future<void> _reset() async {
    await c.cancel();
    if (mounted) await c.load();
  }

  @override
  void dispose() {
    code.dispose();
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final state = c.snapshot;
        final editable = state?.supported == true &&
            {'closed', 'failed', 'expired', 'rejected', 'cancelled'}
                .contains(state!.state);
        return Card(
            child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Pair with a short code',
                          style: Theme.of(context).textTheme.titleMedium),
                      if (widget.backend.isDemo)
                        const Text(
                            'Preview only — no real identities, permissions or connections are changed.'),
                      if (state?.supported == false)
                        const Text(
                            'Short-code pairing is awaiting security review. Existing paired devices remain available below. No code, certificate transfer or terminal command is required on this screen.'),
                      if (widget.address.isNotEmpty)
                        Text('Selected device: ${widget.address}'),
                      if (editable) ...[
                        const Text(
                            'Enter the six-digit code displayed by the other computer.'),
                        TextField(
                            key: const ValueKey('pairing-code'),
                            controller: code,
                            enabled: !c.busy,
                            keyboardType: TextInputType.number,
                            maxLength: 6,
                            autocorrect: false,
                            enableSuggestions: false,
                            inputFormatters: [
                              FilteringTextInputFormatter.digitsOnly
                            ],
                            decoration: const InputDecoration(
                                labelText: 'Six-digit pairing code'),
                            onChanged: (_) => setState(() {})),
                        FilledButton(
                            onPressed: c.busy ||
                                    code.text.length != 6 ||
                                    widget.address.isEmpty
                                ? null
                                : () async {
                                    final value = code.text;
                                    code.clear();
                                    await c.join(widget.address, value);
                                  },
                            child: const Text('Pair device')),
                      ],
                      if (state?.supported == true && !editable)
                        Text(switch (state!.state) {
                          'paired_no_access' =>
                            'Identity paired. No execution access has been granted. Review the execution target separately.',
                          'awaiting_approval' =>
                            'Waiting for approval on the other computer.',
                          'ready' =>
                            'Pairing component reports ready. Execution still follows the configured policy.',
                          'incomplete' =>
                            'Pairing is incomplete. Inspect both devices before retrying.',
                          _ => 'Pairing status: ${state.state}',
                        }),
                      if (c.busy)
                        const LinearProgressIndicator(
                            semanticsLabel: 'Checking device pairing'),
                      if (c.error.isNotEmpty)
                        Text(c.error,
                            style: TextStyle(
                                color: Theme.of(context).colorScheme.error)),
                      if (c.message.isNotEmpty) Text(c.message),
                      Wrap(spacing: 8, children: [
                        TextButton(
                            onPressed: c.busy ? null : c.load,
                            child: const Text('Refresh pairing status')),
                        if (c.busy || state?.supported == true)
                          TextButton(
                              onPressed: () async {
                                code.clear();
                                await c.cancel();
                              },
                              child: const Text('Cancel pairing')),
                      ]),
                    ])));
      });
}
