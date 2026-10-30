import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';
import '../controllers/collaboration_setting_controller.dart';

class CollaborationSettingDialog extends StatefulWidget {
  const CollaborationSettingDialog(
      {super.key,
      required this.session,
      this.rounds = false,
      this.enablement = false});
  final DesktopSession session;
  final bool rounds;
  final bool enablement;
  @override
  State<CollaborationSettingDialog> createState() =>
      _CollaborationSettingDialogState();
}

class _CollaborationSettingDialogState
    extends State<CollaborationSettingDialog> {
  late final CollaborationSettingController controller;
  final input = TextEditingController();
  String? validation;
  bool enabled = false;
  @override
  void initState() {
    super.initState();
    controller = CollaborationSettingController(widget.session,
        rounds: widget.rounds, enablement: widget.enablement);
    load();
  }

  Future<void> load() async {
    await controller.load();
    if (mounted && controller.version != null) {
      if (widget.enablement) {
        setState(() => enabled = controller.version![controller.field] == true);
      } else {
        input.text = '${controller.version![controller.field]}';
      }
    }
  }

  @override
  void dispose() {
    input.dispose();
    controller.dispose();
    super.dispose();
  }

  Future<void> save() async {
    final value =
        widget.enablement ? (enabled ? 1 : 0) : int.tryParse(input.text.trim());
    final min = widget.rounds || widget.enablement ? 0 : 1,
        max = widget.enablement
            ? 1
            : widget.rounds
                ? 32
                : 100000;
    if (value == null || value < min || value > max) {
      setState(() => validation = 'Enter a whole number from $min to $max.');
      return;
    }
    setState(() => validation = null);
    if (await controller.save(value) && mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        return PopScope(
            canPop: !c.loading && !c.saving,
            child: AlertDialog(
              title: Text(widget.enablement
                  ? 'Collaboration availability'
                  : widget.rounds
                      ? 'Collaboration round limit'
                      : 'Collaboration context limit'),
              content: SingleChildScrollView(
                  child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                    Text(widget.enablement
                        ? 'Enabling makes collaboration tools available in new sessions. Existing sessions keep their settings. Existing automatic consultation settings remain in effect and may allow automatic consultations. Access and approval rules still apply.'
                        : widget.rounds
                            ? 'Limits collaboration rounds per turn. The lower of this and the consultation limit applies; zero prevents consultations. Applies to new sessions and does not enable collaboration.'
                            : 'Limits characters in each collaboration question, context and response. Applies to new sessions and does not enable collaboration.'),
                    if (!c.available)
                      const Text(
                          'Unavailable for this connection. Close and reopen after reconnecting.'),
                    if (c.loading || c.saving)
                      const LinearProgressIndicator(
                          semanticsLabel:
                              'Loading or saving collaboration setting'),
                    if (widget.enablement && c.version != null)
                      SwitchListTile(
                          contentPadding: EdgeInsets.zero,
                          title: const Text('Enable collaboration'),
                          value: enabled,
                          onChanged: c.available &&
                                  c.version != null &&
                                  !c.loading &&
                                  !c.saving
                              ? (value) => setState(() => enabled = value)
                              : null)
                    else if (!widget.enablement)
                      TextField(
                          controller: input,
                          enabled: c.available &&
                              c.version != null &&
                              !c.loading &&
                              !c.saving,
                          keyboardType: TextInputType.number,
                          decoration: InputDecoration(
                              labelText: widget.rounds
                                  ? 'Maximum rounds'
                                  : 'Maximum characters')),
                    if (validation != null) Text(validation!),
                    if (c.error != null) Text(c.error!),
                  ])),
              actions: [
                TextButton(
                    onPressed: c.loading || c.saving
                        ? null
                        : () => Navigator.pop(context),
                    child: const Text('Cancel')),
                if (c.version == null)
                  TextButton(
                      onPressed:
                          c.available && !c.loading && !c.saving ? load : null,
                      child: const Text('Reload')),
                FilledButton(
                    onPressed: c.available &&
                            c.version != null &&
                            !c.loading &&
                            !c.saving
                        ? save
                        : null,
                    child: const Text('Save')),
              ],
            ));
      });
}
