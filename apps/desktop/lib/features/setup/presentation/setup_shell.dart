import 'package:flutter/material.dart';

import '../../../app/theme.dart';

/// A single branded surface for each setup point: header, bounded content, and a
/// fixed footer with exactly one primary action.
class SetupShell extends StatelessWidget {
  const SetupShell({
    super.key,
    required this.title,
    required this.subtitle,
    required this.child,
    this.stepLabel,
    this.footer,
    this.maxContentWidth = 760,
    this.brandBadge = true,
  });

  final String title, subtitle;
  final String? stepLabel;
  final Widget child;
  final Widget? footer;
  final double maxContentWidth;
  final bool brandBadge;

  @override
  Widget build(BuildContext context) => Material(
      color: Theme.of(context).scaffoldBackgroundColor,
      // The shell sets its own material; pin the default text style to the
      // bundled family so inherited labels never fall back to a missing font.
      child: DefaultTextStyle(
          style: Theme.of(context).textTheme.bodyMedium!,
          child: SafeArea(
              child: Column(children: [
            _header(context),
            const Divider(height: 1),
            Expanded(
                child: SingleChildScrollView(
                    padding: const EdgeInsets.symmetric(
                        horizontal: OdessyTokens.spaceLarge,
                        vertical: OdessyTokens.spaceLarge),
                    child: Center(
                        child: ConstrainedBox(
                            constraints:
                                BoxConstraints(maxWidth: maxContentWidth),
                            child: child)))),
            if (footer != null) ...[
              const Divider(height: 1),
              Padding(
                  padding: const EdgeInsets.symmetric(
                      horizontal: OdessyTokens.spaceLarge,
                      vertical: OdessyTokens.spaceRegular),
                  child: Center(
                      child: ConstrainedBox(
                          constraints: BoxConstraints(maxWidth: maxContentWidth),
                          child: footer!))),
            ],
          ]))));

  Widget _header(BuildContext context) => Padding(
      padding: const EdgeInsets.symmetric(
          horizontal: OdessyTokens.spaceLarge, vertical: OdessyTokens.spaceRegular),
      child: Center(
          child: ConstrainedBox(
              constraints: BoxConstraints(maxWidth: maxContentWidth),
              child: Row(children: [
                if (brandBadge) ...[
                  const OdessyBrandMark(),
                  const SizedBox(width: OdessyTokens.spaceMedium),
                ],
                Expanded(
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                      Text(title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.titleLarge),
                      if (subtitle.isNotEmpty)
                        Text(subtitle,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context)
                                .textTheme
                                .bodySmall
                                ?.copyWith(color: Colors.grey)),
                    ])),
                if (stepLabel != null)
                  Padding(
                      padding: const EdgeInsets.only(
                          left: OdessyTokens.spaceMedium),
                      child: Text(stepLabel!,
                          style: Theme.of(context).textTheme.labelMedium)),
              ]))));
}

/// The in-app brand mark. The Windows `odessy-boat.ico` runner resources are a
/// native packaging change; this keeps the same restrained identity in-app.
class OdessyBrandMark extends StatelessWidget {
  const OdessyBrandMark({super.key, this.size = 40});
  final double size;
  @override
  Widget build(BuildContext context) => Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
          color: OdessyTokens.accent.withValues(alpha: .16),
          borderRadius: BorderRadius.circular(size / 4)),
      alignment: Alignment.center,
      child: Icon(Icons.sailing_outlined,
          size: size * .58, color: OdessyTokens.accent));
}

/// Footer action row. Exactly one primary action; secondary actions stay left.
/// At narrow widths the actions stack instead of wrapping unevenly.
class SetupFooter extends StatelessWidget {
  const SetupFooter({
    super.key,
    required this.primaryLabel,
    required this.onPrimary,
    this.secondaryLabel,
    this.onSecondary,
    this.busy = false,
    this.primaryIsDestructive = false,
    this.note,
  });

  final String primaryLabel;
  final VoidCallback? onPrimary;
  final String? secondaryLabel;
  final VoidCallback? onSecondary;
  final bool busy, primaryIsDestructive;
  final String? note;

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        // 44-pixel control height via minimum size, never a fixed box: a fixed
        // height combined with the theme's padding clips the label glyphs.
        final primary = SizedBox(
            height: 44,
            child: primaryIsDestructive
                ? FilledButton(
                    onPressed: busy ? null : onPrimary,
                    style: FilledButton.styleFrom(
                        minimumSize: const Size(0, 44),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 20, vertical: 10),
                        backgroundColor: Theme.of(context).colorScheme.error,
                        foregroundColor: Theme.of(context).colorScheme.onError),
                    child: Text(primaryLabel))
                : FilledButton(
                    onPressed: busy ? null : onPrimary,
                    style: FilledButton.styleFrom(
                        minimumSize: const Size(0, 44),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 20, vertical: 10)),
                    child: Text(primaryLabel)));
        final secondary = secondaryLabel == null
            ? null
            : TextButton(
                onPressed: busy ? null : onSecondary,
                style: TextButton.styleFrom(
                    minimumSize: const Size(0, 44),
                    padding:
                        const EdgeInsets.symmetric(horizontal: 16, vertical: 10)),
                child: Text(secondaryLabel!));
        final stacked = box.maxWidth < 420;
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (note != null)
            Padding(
                padding: const EdgeInsets.only(bottom: OdessyTokens.spaceCompact),
                child: Text(note!,
                    style: Theme.of(context).textTheme.bodySmall)),
          if (stacked)
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              primary,
              if (secondary != null) ...[
                const SizedBox(height: OdessyTokens.spaceCompact),
                secondary,
              ],
            ])
          else
            Row(children: [
              if (secondary != null) secondary,
              const Spacer(),
              primary,
            ]),
        ]);
      });
}

/// A labelled group used by setup and settings; keeps the 8-pixel rhythm.
class SetupSection extends StatelessWidget {
  const SetupSection(
      {super.key, required this.title, this.description, required this.child});
  final String title;
  final String? description;
  final Widget child;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.only(bottom: OdessyTokens.spaceLarge),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: Theme.of(context).textTheme.titleMedium),
        if (description != null) ...[
          const SizedBox(height: OdessyTokens.spaceSmall),
          Text(description!,
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: Colors.grey)),
        ],
        const SizedBox(height: OdessyTokens.spaceMedium),
        child,
      ]));
}

/// Inline status for errors and notices: reason plus one safe action.
class SetupMessage extends StatelessWidget {
  const SetupMessage(
      {super.key,
      required this.text,
      this.details,
      this.actionLabel,
      this.onAction,
      this.error = false});
  final String text;
  final String? details;
  final String? actionLabel;
  final VoidCallback? onAction;
  final bool error;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: OdessyTokens.spaceMedium),
        padding: const EdgeInsets.all(OdessyTokens.spaceMedium),
        decoration: BoxDecoration(
            color: error
                ? scheme.errorContainer
                : scheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(OdessyTokens.radiusSmall)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(text),
          if (details != null) ...[
            const SizedBox(height: OdessyTokens.spaceCompact),
            Text(details!,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(fontSize: 11)),
          ],
          if (actionLabel != null && onAction != null)
            Align(
                alignment: Alignment.centerLeft,
                child: TextButton(
                    onPressed: onAction, child: Text(actionLabel!))),
        ]));
  }
}

/// Format a duration for stage rows: `4s`, `1m 05s`, `1h 02m`.
String formatElapsed(Duration? value) {
  if (value == null) return '—';
  final seconds = value.inSeconds;
  if (seconds < 60) return '${seconds}s';
  if (seconds < 3600) {
    return '${seconds ~/ 60}m ${(seconds % 60).toString().padLeft(2, '0')}s';
  }
  return '${seconds ~/ 3600}h ${((seconds % 3600) ~/ 60).toString().padLeft(2, '0')}m';
}
