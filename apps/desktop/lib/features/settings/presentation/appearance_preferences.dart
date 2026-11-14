import 'package:flutter/material.dart';

import '../../../app/preferences/desktop_preferences.dart';
import '../controllers/desktop_preferences_controller.dart';

/// Real, durable appearance preferences for the Settings "General" section.
///
/// Every value here is presentation state owned by the app, not the service.
/// Save/Cancel/Reload act on the local store; a preview is applied immediately
/// through [onPreview] while the durable write still requires Save.
class AppearancePreferences extends StatefulWidget {
  const AppearancePreferences({
    super.key,
    required this.controller,
    this.onPreview,
  });

  final DesktopPreferencesController controller;

  /// Called after each draft edit so the app can render a live preview.
  final VoidCallback? onPreview;

  @override
  State<AppearancePreferences> createState() => _AppearancePreferencesState();
}

class _AppearancePreferencesState extends State<AppearancePreferences> {
  @override
  void initState() {
    super.initState();
    final controller = widget.controller;
    if (!controller.loaded && !controller.loading) controller.load();
  }

  void _edit(DesktopPreferences next) {
    widget.controller.edit(next);
    widget.onPreview?.call();
  }

  Future<void> _reload() async {
    if (!widget.controller.dirty) {
      await widget.controller.load();
      return;
    }
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('Discard appearance changes?'),
                content: const Text(
                    'The draft will be replaced with the saved preferences.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('Keep editing')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Reload')),
                ]));
    if (confirmed == true && mounted) await widget.controller.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        final value = controller.value;
        final busy = controller.loading || controller.saving;
        return Padding(
            padding: const EdgeInsets.only(bottom: 24),
            child: Card(
                key: const Key('ui-preferences'),
                child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('Appearance',
                              style: TextStyle(
                                  fontSize: 15, fontWeight: FontWeight.w600)),
                          const SizedBox(height: 8),
                          const Text(
                              'Stored for your user account on this device. These values are not part of the installation, the service configuration or any credential store.',
                              style: TextStyle(fontSize: 12)),
                          const SizedBox(height: 16),
                          const Text('Theme', style: TextStyle(fontSize: 13)),
                          const SizedBox(height: 8),
                          SegmentedButton<DesktopTheme>(
                              key: const Key('ui-preferences-theme'),
                              segments: const [
                                ButtonSegment(
                                    value: DesktopTheme.system,
                                    label: Text('System')),
                                ButtonSegment(
                                    value: DesktopTheme.light,
                                    label: Text('Light')),
                                ButtonSegment(
                                    value: DesktopTheme.dark,
                                    label: Text('Dark')),
                              ],
                              selected: {value.theme},
                              onSelectionChanged: busy
                                  ? null
                                  : (selection) => _edit(DesktopPreferences(
                                      theme: selection.first,
                                      textScale: value.textScale,
                                      reduceTransparency:
                                          value.reduceTransparency))),
                          const SizedBox(height: 20),
                          Text(
                              'Text size · ${(value.textScale * 100).round()}%',
                              style: const TextStyle(fontSize: 13)),
                          Slider(
                              key: const Key('ui-preferences-scale'),
                              value: value.textScale.clamp(1.0, 1.6),
                              min: 1.0,
                              max: 1.6,
                              divisions: 6,
                              label: '${(value.textScale * 100).round()}%',
                              onChanged: busy
                                  ? null
                                  : (scale) => _edit(DesktopPreferences(
                                      theme: value.theme,
                                      textScale: double.parse(
                                          scale.toStringAsFixed(2)),
                                      reduceTransparency:
                                          value.reduceTransparency))),
                          const Text(
                              'App text scales from this value; your operating system text size still applies.',
                              style: TextStyle(fontSize: 11)),
                          const SizedBox(height: 8),
                          SwitchListTile(
                              key: const Key('ui-preferences-transparency'),
                              contentPadding: EdgeInsets.zero,
                              value: value.reduceTransparency,
                              title: const Text(
                                  'Reduce transparency (opaque surfaces)',
                                  style: TextStyle(fontSize: 13)),
                              onChanged: busy
                                  ? null
                                  : (reduce) => _edit(DesktopPreferences(
                                      theme: value.theme,
                                      textScale: value.textScale,
                                      reduceTransparency: reduce))),
                          const SizedBox(height: 12),
                          Container(
                              width: double.infinity,
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(
                                  color: Theme.of(context)
                                      .colorScheme
                                      .surfaceContainerHighest,
                                  borderRadius: BorderRadius.circular(8)),
                              child: Text(
                                  'Preview — Odessy keeps readable contrast for this text.',
                                  key: const Key('ui-preferences-preview'),
                                  style: TextStyle(
                                      fontSize: 13 *
                                          value.textScale.clamp(1.0, 1.6)))),
                          if (controller.dirty)
                            const Padding(
                                padding: EdgeInsets.only(top: 12),
                                child: Text('Unsaved appearance changes',
                                    key: Key('ui-preferences-unsaved'),
                                    style: TextStyle(fontSize: 12))),
                          if (controller.error.isNotEmpty)
                            Padding(
                                padding: const EdgeInsets.only(top: 12),
                                child: Text(controller.error,
                                    key: const Key('ui-preferences-error'),
                                    style: TextStyle(
                                        fontSize: 12,
                                        color: Theme.of(context)
                                            .colorScheme
                                            .error))),
                          if (controller.status.isNotEmpty)
                            Padding(
                                padding: const EdgeInsets.only(top: 12),
                                child: Text(controller.status,
                                    key: const Key('ui-preferences-status'),
                                    style: const TextStyle(fontSize: 12))),
                          const SizedBox(height: 16),
                          Wrap(spacing: 10, runSpacing: 8, children: [
                            FilledButton(
                                key: const Key('ui-preferences-save'),
                                onPressed: controller.canSave
                                    ? () => controller.save()
                                    : null,
                                child: const Text('Save appearance')),
                            OutlinedButton(
                                key: const Key('ui-preferences-discard'),
                                onPressed: controller.dirty && !busy
                                    ? () => controller.discard()
                                    : null,
                                child: const Text('Cancel')),
                            OutlinedButton(
                                key: const Key('ui-preferences-reload'),
                                onPressed: busy ? null : _reload,
                                child: const Text('Reload')),
                          ]),
                          if (controller.saving)
                            const Padding(
                                padding: EdgeInsets.only(top: 12),
                                child: LinearProgressIndicator()),
                          const SizedBox(height: 12),
                          const Text(
                              'Applies immediately to this app window where supported. It never changes system accessibility settings.',
                              style: TextStyle(fontSize: 11)),
                        ]))));
      });
}
