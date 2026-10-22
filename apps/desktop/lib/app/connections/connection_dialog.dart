import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

Future<OdessyApi?> showConnectionDialog(
        BuildContext context, CredentialStore store) =>
    showDialog<OdessyApi>(
        context: context, builder: (_) => _ConnectionDialog(store: store));

class _ConnectionDialog extends StatefulWidget {
  const _ConnectionDialog({required this.store});
  final CredentialStore store;
  @override
  State<_ConnectionDialog> createState() => _ConnectionDialogState();
}

class _ConnectionDialogState extends State<_ConnectionDialog> {
  final endpoint = TextEditingController(text: 'http://127.0.0.1:8765');
  final token = TextEditingController();
  String error = '';
  bool submitting = false;
  int revision = 0;
  @override
  void initState() {
    super.initState();
    _restore();
  }

  Future<void> _restore() async {
    final captured = revision;
    try {
      final cached = await widget.store.read(endpoint.text);
      if (mounted && revision == captured && token.text.isEmpty) {
        token.text = cached ?? '';
      }
    } catch (_) {
      if (mounted) {
        setState(() => error =
            'Credential storage is unavailable. Enter a token for this connection.');
      }
    }
  }

  @override
  void dispose() {
    endpoint.dispose();
    token.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    if (submitting) return;
    setState(() {
      submitting = true;
      error = '';
    });
    LocalApi? api;
    try {
      final address = endpoint.text.trim();
      api = LocalApi(Uri.parse(address), token.text);
      await widget.store.write(address, token.text);
      if (!mounted) {
        api.close();
        return;
      }
      Navigator.pop(context, api);
    } catch (_) {
      api?.close();
      if (mounted) {
        setState(() {
          submitting = false;
          error =
              'Use a valid loopback address and token. Credential storage must be available.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
          title: const Text('Connect to Odessy'),
          content: SizedBox(
              width: 420,
              child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(widget.store.persistsAcrossRestarts
                        ? 'Connect to the local Odessy service using its bearer token.'
                        : 'Use the address and token from odessy serve. Tokens stay in memory only. Remote engines require a preconfigured local tunnel.'),
                    const SizedBox(height: 20),
                    TextField(
                        key: const Key('connection-endpoint'),
                        controller: endpoint,
                        enabled: !submitting,
                        onChanged: (_) {
                          revision++;
                          token.clear();
                        },
                        decoration: const InputDecoration(
                            labelText: 'Local API address')),
                    const SizedBox(height: 12),
                    TextField(
                        key: const Key('connection-token'),
                        controller: token,
                        enabled: !submitting,
                        obscureText: true,
                        enableSuggestions: false,
                        autocorrect: false,
                        onChanged: (_) {
                          revision++;
                        },
                        decoration:
                            const InputDecoration(labelText: 'Bearer token')),
                    if (error.isNotEmpty)
                      Padding(
                          padding: const EdgeInsets.only(top: 12),
                          child: Text(error,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error))),
                  ])),
          actions: [
            TextButton(
                onPressed:
                    submitting ? null : () => Navigator.pop(context, DemoApi()),
                child: const Text('Use demo')),
            TextButton(
                onPressed: submitting ? null : () => Navigator.pop(context),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: submitting ? null : _connect,
                child: Text(submitting ? 'Connecting…' : 'Connect')),
          ]);
}
