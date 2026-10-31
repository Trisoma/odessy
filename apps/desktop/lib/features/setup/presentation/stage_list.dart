import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../controllers/setup_stage.dart';
import 'setup_shell.dart';

/// Stage rows with state, elapsed time for the active stage and duration for a
/// completed one. No invented remaining-time estimates.
class StageList extends StatelessWidget {
  const StageList({super.key, required this.stages, this.now});
  final List<SetupStage> stages;
  final DateTime? now;

  @override
  Widget build(BuildContext context) => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [for (final stage in stages) _row(context, stage)]);

  Widget _row(BuildContext context, SetupStage stage) {
    final scheme = Theme.of(context).colorScheme;
    final running = stage.state == SetupStageState.running;
    final colour = switch (stage.state) {
      SetupStageState.failed => scheme.error,
      SetupStageState.complete => OdessyTokens.accent,
      SetupStageState.cancelled => Colors.grey,
      _ => scheme.onSurfaceVariant,
    };
    final started = stage.startedAt;
    final finished = stage.finishedAt ?? now ?? DateTime.now();
    final elapsed = started == null ? null : finished.difference(started);
    final timing = stage.state == SetupStageState.complete || running
        ? formatElapsed(elapsed)
        : '';
    return Semantics(
        label:
            '${stage.label}, ${stage.state.name}${timing.isEmpty ? '' : ', $timing'}',
        child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(children: [
              SizedBox(
                  width: 22,
                  child: running
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : Icon(_icon(stage.state), size: 16, color: colour)),
              const SizedBox(width: OdessyTokens.spaceCompact),
              Expanded(
                  child: Text(stage.label,
                      style: TextStyle(
                          color: stage.state == SetupStageState.waiting
                              ? Colors.grey
                              : null,
                          fontWeight: running ? FontWeight.w600 : null))),
              if (timing.isNotEmpty)
                Text(timing,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        fontFeatures: const [FontFeature.tabularFigures()])),
            ])));
  }

  IconData _icon(SetupStageState state) => switch (state) {
        SetupStageState.complete => Icons.check_circle,
        SetupStageState.failed => Icons.error_outline,
        SetupStageState.cancelled => Icons.remove_circle_outline,
        SetupStageState.running => Icons.timelapse,
        SetupStageState.waiting => Icons.radio_button_unchecked,
      };
}

/// Bounded, collapsible installation output inside the same window. It is a
/// visual log, never an interactive shell.
class InstallOutputPane extends StatefulWidget {
  const InstallOutputPane(
      {super.key,
      required this.lines,
      this.running = false,
      this.onExport,
      this.maxHeight = 220});
  final List<String> lines;
  final bool running;
  final VoidCallback? onExport;
  final double maxHeight;

  @override
  State<InstallOutputPane> createState() => _InstallOutputPaneState();
}

class _InstallOutputPaneState extends State<InstallOutputPane> {
  final scroll = ScrollController();
  bool autoscroll = true;

  void _toEnd() {
    if (!autoscroll || !scroll.hasClients) return;
    scroll.jumpTo(scroll.position.maxScrollExtent);
  }

  @override
  void didUpdateWidget(covariant InstallOutputPane oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.lines.length != oldWidget.lines.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _toEnd());
    }
  }

  @override
  void dispose() {
    scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Container(
      margin: const EdgeInsets.only(top: OdessyTokens.spaceMedium),
      decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).dividerColor),
          borderRadius: BorderRadius.circular(OdessyTokens.radiusSmall)),
      child: ExpansionTile(
          key: const Key('install-output'),
          tilePadding:
              const EdgeInsets.symmetric(horizontal: OdessyTokens.spaceMedium),
          title: Text('Show installation output (${widget.lines.length})',
              style: Theme.of(context).textTheme.labelLarge),
          children: [
            Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: OdessyTokens.spaceMedium),
                child: Row(children: [
                  Switch(
                      value: autoscroll,
                      onChanged: (value) => setState(() => autoscroll = value)),
                  const Text('Follow output', style: TextStyle(fontSize: 12)),
                  const Spacer(),
                  if (widget.onExport != null)
                    TextButton(
                        onPressed: widget.onExport,
                        child: const Text('Export log')),
                ])),
            Container(
                constraints: BoxConstraints(maxHeight: widget.maxHeight),
                width: double.infinity,
                margin: const EdgeInsets.fromLTRB(OdessyTokens.spaceMedium, 0,
                    OdessyTokens.spaceMedium, OdessyTokens.spaceMedium),
                padding: const EdgeInsets.all(OdessyTokens.spaceCompact),
                decoration: BoxDecoration(
                    color:
                        Theme.of(context).colorScheme.surfaceContainerHighest,
                    borderRadius:
                        BorderRadius.circular(OdessyTokens.radiusSmall)),
                child: widget.lines.isEmpty
                    ? const Text('No output yet.',
                        style: TextStyle(fontSize: 11))
                    : SingleChildScrollView(
                        controller: scroll,
                        child: SelectableText(widget.lines.join('\n'),
                            key: const Key('install-output-text'),
                            style:
                                const TextStyle(fontSize: 11, height: 1.45)))),
          ]));
}
