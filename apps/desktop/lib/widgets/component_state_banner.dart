import 'package:flutter/material.dart';
import '../app/theme.dart';

enum BannerTone { information, error }

/// Shared feedback chrome. Callers supply observed facts and explicit actions.
class ComponentStateBanner extends StatelessWidget {
  const ComponentStateBanner(
      {super.key,
      required this.message,
      this.tone = BannerTone.information,
      this.actions = const []});
  final String message;
  final BannerTone tone;
  final List<Widget> actions;
  @override
  Widget build(BuildContext context) {
    final error = tone == BannerTone.error;
    final colors = Theme.of(context).colorScheme;
    return Semantics(
        liveRegion: true,
        container: true,
        child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
            decoration: BoxDecoration(
                color: error
                    ? colors.errorContainer
                    : colors.surfaceContainerHighest,
                border:
                    Border(bottom: BorderSide(color: colors.outlineVariant))),
            child: LayoutBuilder(builder: (context, box) {
              final content =
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(error ? Icons.error_outline : Icons.info_outline,
                    size: 20,
                    color: error
                        ? colors.onErrorContainer
                        : colors.onSurfaceVariant),
                const SizedBox(width: 12),
                Expanded(
                    child: Text(message,
                        style: TextStyle(
                            color: error
                                ? colors.onErrorContainer
                                : colors.onSurfaceVariant))),
              ]);
              final buttons =
                  Wrap(spacing: 8, runSpacing: 8, children: actions);
              if (box.maxWidth >= 600 &&
                  MediaQuery.textScalerOf(context).scale(14) <= 21) {
                return Row(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      Expanded(flex: 3, child: content),
                      if (actions.isNotEmpty) ...[
                        const SizedBox(width: 16),
                        Flexible(flex: 2, child: buttons),
                      ],
                    ]);
              }
              return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    content,
                    if (actions.isNotEmpty) ...[
                      const SizedBox(height: OdessyTokens.spaceCompact),
                      buttons,
                    ],
                  ]);
            })));
  }
}
