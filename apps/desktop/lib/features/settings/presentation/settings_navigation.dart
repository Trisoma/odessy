import 'package:flutter/material.dart';
import '../controllers/component_settings_controller.dart';
import '../controllers/settings_form_controller.dart';

Future<bool> confirmFormNavigation(
    BuildContext context, SettingsFormController<int>? form) async {
  if (form == null || form.loading || !form.dirty) return true;
  final origin = FocusManager.instance.primaryFocus;
  final action = await showDialog<String>(
      context: context,
      requestFocus: true,
      traversalEdgeBehavior: TraversalEdgeBehavior.closedLoop,
      builder: (context) => AlertDialog(
              title: const Text('Unsaved collaboration setting'),
              content:
                  const Text('Save this change, discard it, or keep editing.'),
              actions: [
                TextButton(
                    autofocus: true,
                    onPressed: () => Navigator.pop(context, 'keep'),
                    child: const Text('Keep editing')),
                TextButton(
                    onPressed: () => Navigator.pop(context, 'discard'),
                    child: const Text('Discard')),
                FilledButton(
                    onPressed: form.canSave
                        ? () => Navigator.pop(context, 'save')
                        : null,
                    child: const Text('Save')),
              ]));
  if (context.mounted && origin?.context != null) origin!.requestFocus();
  if (action == 'discard') {
    await form.load();
    return form.error == null;
  }
  if (action == 'save') {
    await form.save();
    return !form.dirty && form.error == null;
  }
  return false;
}

/// Only explicit Save admits a mutation; leaving an admitted save never cancels it.
Future<bool> confirmSettingsNavigation(
    BuildContext context, ComponentSettingsController? controller) async {
  final c = controller;
  if (c == null || c.busy || !c.dirty) return true;
  final section = c.activeSection!;
  final origin = FocusManager.instance.primaryFocus;
  final answer = await showDialog<String>(
      context: context,
      requestFocus: true,
      traversalEdgeBehavior: TraversalEdgeBehavior.closedLoop,
      builder: (context) => AlertDialog(
              title: const Text('Unsaved changes'),
              content: const Text(
                  'Save this section, discard its edits, or keep editing. Other sections are unchanged.'),
              actions: [
                TextButton(
                    autofocus: true,
                    onPressed: () => Navigator.pop(context, 'keep'),
                    child: const Text('Keep editing')),
                TextButton(
                    onPressed: () => Navigator.pop(context, 'discard'),
                    child: const Text('Discard')),
                FilledButton(
                    onPressed: c.inspectionRequired
                        ? null
                        : () => Navigator.pop(context, 'save'),
                    child: const Text('Save')),
              ]));
  if (context.mounted && origin?.context != null) origin!.requestFocus();
  if (answer == 'discard') {
    c.discard(section);
    return true;
  }
  if (answer == 'save') return c.save(section, c.drafts[section]!.values);
  return false;
}
