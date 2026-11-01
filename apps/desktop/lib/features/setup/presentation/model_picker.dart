import 'package:flutter/material.dart';
import '../controllers/model_catalog_controller.dart';

/// Display names select exact backend IDs. Manual entry remains available.
class ModelPicker extends StatefulWidget {
  const ModelPicker(
      {super.key,
      required this.catalog,
      required this.provider,
      required this.value,
      required this.onChanged,
      this.endpoint = '',
      this.credential = '',
      this.enabled = true,
      this.fieldKey});
  final ModelCatalogController catalog;
  final TextEditingController value;
  final String provider, endpoint, credential;
  final VoidCallback onChanged;
  final bool enabled;
  final Key? fieldKey;
  @override
  State<ModelPicker> createState() => _ModelPickerState();
}

class _ModelPickerState extends State<ModelPicker> {
  @override
  void initState() {
    super.initState();
    widget.catalog.addListener(_refresh);
    _schedule();
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  void _schedule() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _load();
    });
  }

  void _load({bool refresh = false}) => widget.catalog.load(widget.provider,
      endpoint: widget.endpoint,
      credential: widget.credential,
      refresh: refresh);
  @override
  void didUpdateWidget(covariant ModelPicker old) {
    super.didUpdateWidget(old);
    if (old.provider != widget.provider ||
        old.endpoint != widget.endpoint ||
        old.credential != widget.credential) {
      _schedule();
    }
  }

  @override
  void dispose() {
    widget.catalog.removeListener(_refresh);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.catalog;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      if (c.models.isNotEmpty)
        DropdownButtonFormField<String>(
            key: ValueKey(
                '${widget.provider}-${c.models.map((m) => m['name']).join(',')}-${widget.value.text}'),
            isExpanded: true,
            initialValue: c.models.any((m) => m['name'] == widget.value.text)
                ? widget.value.text
                : null,
            decoration: const InputDecoration(labelText: 'Choose a model'),
            items: [
              for (final m in c.models)
                DropdownMenuItem(
                    value: m['name'] as String,
                    child: Text((m['label'] ?? m['name']) as String,
                        overflow: TextOverflow.ellipsis))
            ],
            onChanged: widget.enabled && !c.busy
                ? (value) {
                    widget.value.text = value ?? '';
                    widget.onChanged();
                    setState(() {});
                  }
                : null),
      const SizedBox(height: 8),
      TextField(
          key: widget.fieldKey,
          controller: widget.value,
          enabled: widget.enabled,
          onChanged: (_) => widget.onChanged(),
          decoration: const InputDecoration(
              labelText: 'Model ID (or enter a custom ID)',
              helperText:
                  'Choosing a model above fills its exact API identifier.')),
      OutlinedButton.icon(
          onPressed:
              widget.enabled && !c.busy ? () => _load(refresh: true) : null,
          icon: const Icon(Icons.refresh, size: 18),
          label: Text(
              c.busy ? 'Reading model choices…' : 'Fetch available models')),
      if (c.message.isNotEmpty) Text(c.message),
      const Text(
          'Fetching reads model metadata only; it does not run inference or download models.',
          style: TextStyle(fontSize: 11)),
    ]);
  }
}
