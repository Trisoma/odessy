import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Displays assistant text and fenced code without executing or interpreting it.
class MessageContent extends StatelessWidget {
  const MessageContent({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final parts = _splitFencedCode(text);
    if (parts.length == 1 && parts.single is _TextPart) {
      return Text(_formatProse(text), style: const TextStyle(height: 1.6));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final part in parts)
          switch (part) {
            _TextPart(:final text) =>
              Text(_formatProse(text), style: const TextStyle(height: 1.6)),
            _CodePart(:final code, :final language) =>
              _CodeBlock(code: code, language: language),
          },
      ],
    );
  }
}

String _formatProse(String text) {
  // Keep an unfinished code fence completely literal while a reply streams.
  if (RegExp(r'^[ ]{0,3}([\x60]{3,}|~{3,})', multiLine: true).hasMatch(text)) {
    return text;
  }
  return text.replaceAllMapped(
      RegExp(r'^([ ]{0,3})[-*+]([ \t]+)(?=\S)', multiLine: true),
      (match) => '${match[1]}•${match[2]}');
}

sealed class _Part {}

class _TextPart extends _Part {
  _TextPart(this.text);
  final String text;
}

class _CodePart extends _Part {
  _CodePart(this.code, this.language);
  final String code;
  final String language;
}

List<_Part> _splitFencedCode(String text) {
  final parts = <_Part>[];
  final opening =
      RegExp(r'^[ ]{0,3}([\x60]{3,}|~{3,})([^\r\n]*)\r?\n', multiLine: true);
  var position = 0;
  while (position < text.length) {
    final start = opening.firstMatch(text.substring(position));
    if (start == null) break;
    final startOffset = position + start.start;
    final codeStart = position + start.end;
    final fence = start.group(1)!;
    final closing = RegExp(
        '^[ ]{0,3}${RegExp.escape(fence[0])}{${fence.length},}[ \\t]*'
        r'(?:\r?\n|$)',
        multiLine: true);
    final end = closing.firstMatch(text.substring(codeStart));
    if (end == null) break;
    if (startOffset > position) {
      parts.add(_TextPart(text.substring(position, startOffset)));
    }
    final rawInfo = start.group(2)!.trim();
    final info = rawInfo.isEmpty ? '' : rawInfo.split(RegExp(r'\s+')).first;
    final language =
        RegExp(r'^[A-Za-z0-9_+.#-]{1,32}$').hasMatch(info) ? info : 'Code';
    parts.add(
        _CodePart(text.substring(codeStart, codeStart + end.start), language));
    position = codeStart + end.end;
  }
  if (position < text.length || parts.isEmpty) {
    parts.add(_TextPart(text.substring(position)));
  }
  return parts;
}

class _CodeBlock extends StatelessWidget {
  const _CodeBlock({required this.code, required this.language});
  final String code;
  final String language;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 8),
      decoration: BoxDecoration(
          color: colors.surfaceContainerLow,
          border: Border.all(color: colors.outlineVariant),
          borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Padding(
            padding: const EdgeInsets.only(left: 16, right: 4, top: 4),
            child: Row(children: [
              Expanded(
                  child: Text(language,
                      style: Theme.of(context)
                          .textTheme
                          .labelMedium
                          ?.copyWith(color: colors.onSurfaceVariant))),
              IconButton(
                  tooltip: 'Copy code',
                  icon: const Icon(Icons.copy_outlined, size: 16),
                  onPressed: () async {
                    await Clipboard.setData(ClipboardData(text: code));
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Code copied')));
                    }
                  }),
            ])),
        SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                child: Text(code,
                    softWrap: false,
                    style: const TextStyle(
                        fontFamily: 'OdessyMono', height: 1.5)))),
      ]),
    );
  }
}
