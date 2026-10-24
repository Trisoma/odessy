import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../../app/theme.dart';

/// Single-line editable path with Browse, full-path access and inline validation.
///
/// The picker is injected: presentation never touches the filesystem. Text entry
/// stays available for accessibility and advanced use.
class PathField extends StatelessWidget {
  const PathField({
    super.key,
    required this.controller,
    required this.label,
    this.helper,
    this.error,
    this.hint,
    this.enabled = true,
    this.onChanged,
    this.onBrowse,
    this.browseLabel = 'Browse…',
    this.browseTooltip,
    this.fieldKey,
  });

  final TextEditingController controller;
  final String label;
  final String? helper, error, hint, browseLabel, browseTooltip;
  final bool enabled;
  final ValueChanged<String>? onChanged;
  final VoidCallback? onBrowse;
  final Key? fieldKey;

  @override
  Widget build(BuildContext context) {
    final text = controller.text;
    return Padding(
        padding: const EdgeInsets.only(bottom: OdessyTokens.spaceMedium),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
                child: TextField(
                    key: fieldKey,
                    controller: controller,
                    enabled: enabled,
                    maxLines: 1,
                    onChanged: onChanged,
                    style: const TextStyle(fontSize: 13),
                    decoration: InputDecoration(
                        labelText: label,
                        hintText: hint,
                        errorText: error,
                        helperText: helper,
                        helperMaxLines: 2,
                        errorMaxLines: 2))),
            const SizedBox(width: OdessyTokens.spaceCompact),
            Tooltip(
                message: text.isEmpty ? 'No path selected' : 'Full path: $text',
                child: IconButton(
                    tooltip: 'Copy full path',
                    onPressed: text.isEmpty
                        ? null
                        : () => Clipboard.setData(ClipboardData(text: text)),
                    icon: const Icon(Icons.content_copy_outlined, size: 18))),
            if (onBrowse != null)
              SizedBox(
                  height: 44,
                  child: OutlinedButton.icon(
                      key: fieldKey == null
                          ? null
                          : ValueKey('${(fieldKey as ValueKey).value}-browse'),
                      onPressed: enabled ? onBrowse : null,
                      icon: const Icon(Icons.folder_open_outlined, size: 18),
                      label: Text(browseTooltip ?? browseLabel ?? 'Browse…'))),
          ]),
        ]));
  }
}

/// A recorded path shown on one line with a middle ellipsis and the full path on
/// hover/focus. Never a wrapped paragraph.
class SummaryPath extends StatelessWidget {
  const SummaryPath(
      {super.key, required this.path, this.label, this.maxCharacters = 52});
  final String path;
  final String? label;
  final int maxCharacters;

  @override
  Widget build(BuildContext context) => Row(children: [
        if (label != null) ...[
          SizedBox(
              width: 108,
              child: Text(label!,
                  style: Theme.of(context).textTheme.bodySmall)),
        ],
        Expanded(
            child: Tooltip(
                message: path,
                waitDuration: const Duration(milliseconds: 300),
                child: Text(middleEllipsis(path, maxCharacters),
                    maxLines: 1,
                    overflow: TextOverflow.clip,
                    style: const TextStyle(fontSize: 12)))),
        IconButton(
            tooltip: 'Copy full path',
            visualDensity: VisualDensity.compact,
            onPressed: path.isEmpty
                ? null
                : () => Clipboard.setData(ClipboardData(text: path)),
            icon: const Icon(Icons.content_copy_outlined, size: 16)),
      ]);
}

/// Keeps the head and the tail, which is what identifies a long Windows path.
String middleEllipsis(String value, int maxCharacters) {
  if (value.length <= maxCharacters || maxCharacters < 12) return value;
  final head = (maxCharacters - 1) ~/ 2;
  final tail = maxCharacters - 1 - head;
  return '${value.substring(0, head)}…${value.substring(value.length - tail)}';
}
