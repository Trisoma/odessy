import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';
import '../controllers/pairing_controller.dart';

/// The ordinary-user pairing surface: pick a nearby device, type six digits, wait
/// for the other computer's operator to approve, then review execution access.
///
/// Presentation only. No certificate, fingerprint, executable path or command is
/// entered here, and this widget never decides that pairing succeeded.
class ShortCodePanel extends StatefulWidget {
  const ShortCodePanel(
      {super.key,
      required this.backend,
      required this.address,
      this.addressLabel = '',
      this.onPaired});
  final PairingBackend backend;

  /// The address of the selected nearby device. Never a hostname the user typed.
  final String address;
  final String addressLabel;

  /// Called once when an identity becomes enrolled, so the page can offer the
  /// separate **Use for tools** review.
  final VoidCallback? onPaired;

  @override
  State<ShortCodePanel> createState() => _ShortCodePanelState();
}

class _ShortCodePanelState extends State<ShortCodePanel> {
  late final c = PairingController(widget.backend);
  final code = TextEditingController();
  Timer? _poll;
  bool _announced = false;

  @override
  void initState() {
    super.initState();
    unawaited(c.load());
    c.addListener(_watch);
  }

  @override
  void didUpdateWidget(covariant ShortCodePanel old) {
    super.didUpdateWidget(old);
    if (old.address != widget.address) {
      code.clear();
      _stopPolling();
      unawaited(_reset());
    }
  }

  /// Watch for the other computer's operator while the request is pending, and
  /// report the enrollment once so the page can offer the consent review.
  void _watch() {
    final state = c.snapshot;
    if (state == null) return;
    if (state.awaitingApproval) {
      _poll ??= Timer.periodic(const Duration(seconds: 2), (_) {
        unawaited(c.refresh());
      });
    } else {
      _stopPolling();
    }
    if (state.paired && !_announced) {
      _announced = true;
      widget.onPaired?.call();
    }
  }

  void _stopPolling() {
    _poll?.cancel();
    _poll = null;
  }

  Future<void> _reset() async {
    await c.cancel();
    if (mounted) await c.load();
  }

  Future<void> _submit() async {
    final value = code.text;
    await c.join(widget.address, value);
    if (!mounted) return;
    final state = c.snapshot;
    // Keep the code when the attempt did not get through: retyping six digits after
    // a transient failure is exactly the friction this flow exists to remove.
    if (state != null && (state.paired || state.awaitingApproval)) code.clear();
    setState(() {});
  }

  String _describe(PairingSnapshot state) => switch (state.state) {
        'discoverable' =>
          'This computer is showing a code. Enter it on the other computer.',
        'authenticating' => 'Checking the code with the other computer…',
        'awaiting_approval' =>
          'Waiting for the other computer to approve this request. Nothing is shared until its user approves there.',
        'enrolling' => 'Saving the approved access on the other computer…',
        'paired_no_access' =>
          'Identity paired. No execution access was granted. Review “Use for tools” below before anything can run on that device.',
        'ready' =>
          'Identity paired and workspace access approved. Execution still follows the policy of both computers.',
        'expired' =>
          'The six-digit code expired. Ask the other computer for a new code and pair again.',
        'rejected' =>
          'The code was not accepted. Check the digits on the other computer and try again; after three attempts it must start a new pairing window.',
        'cancelled' =>
          'Pairing was cancelled. Nothing was enrolled by this window.',
        'incomplete' =>
          'The last attempt ended before the outcome was known. Check the paired devices on both computers before pairing again; nothing was undone automatically.',
        'failed' =>
          'Pairing did not complete. ${state.message.isEmpty ? 'Check that both computers are on the same network and try again.' : state.message}',
        'closed' => 'No pairing window is open.',
        _ => 'Pairing status: ${state.state}',
      };

  @override
  void dispose() {
    _stopPolling();
    c.removeListener(_watch);
    code.dispose();
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final state = c.snapshot;
        final editable = state == null || (state.supported && state.canStartOver);
        final blocked = state != null && !state.supported;
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
                      if (blocked)
                        const Text(
                            'Short-code pairing is awaiting security review. Existing paired devices remain available below. No code, certificate transfer or terminal command is required on this screen.'),
                      if (!blocked && state != null && state.state != 'closed')
                        Text(_describe(state),
                            key: const ValueKey('pairing-state')),
                      if (state != null &&
                          state.awaitingApproval &&
                          state.secondsRemaining > 0)
                        Text('Request expires in about ${state.secondsRemaining}s.',
                            style: Theme.of(context).textTheme.bodySmall),
                      if (state != null && state.attemptsRemaining != null)
                        Text(
                            'Attempts left in this window: ${state.attemptsRemaining}',
                            style: Theme.of(context).textTheme.bodySmall),
                      if (state != null &&
                          (state.peerAlias.isNotEmpty ||
                              state.peerFingerprint.isNotEmpty))
                        SelectableText(
                            'Requested by ${state.peerAlias.isEmpty ? 'an unnamed device' : state.peerAlias}'
                            '${state.peerFingerprint.isEmpty ? '' : '\nFingerprint ${state.peerFingerprint}'}',
                            style: Theme.of(context).textTheme.bodySmall),
                      if (editable && widget.address.isEmpty)
                        const Text(
                            'Select a nearby device above, or enter its address under Advanced manual enrollment.'),
                      if (editable && widget.address.isNotEmpty)
                        Text(widget.addressLabel.isEmpty
                            ? 'Selected device: ${widget.address}'
                            : 'Selected device: ${widget.addressLabel}'),
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
                                labelText: 'Six-digit pairing code',
                                helperText:
                                    'Leading zeros count: 042137 is six digits.'),
                            onChanged: (_) => setState(() {})),
                        FilledButton(
                            onPressed: c.busy ||
                                    code.text.length != 6 ||
                                    widget.address.isEmpty
                                ? null
                                : _submit,
                            child: const Text('Pair device')),
                      ],
                      if (blocked && widget.address.isNotEmpty)
                        Text('Selected device: ${widget.address}'),
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
                        if (c.busy ||
                            (state?.supported == true &&
                                !state!.paired &&
                                !state.awaitingApproval))
                          TextButton(
                              onPressed: () async {
                                await c.cancel();
                                if (mounted) setState(code.clear);
                              },
                              child: const Text('Cancel pairing')),
                      ]),
                    ])));
      });
}
