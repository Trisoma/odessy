import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/setup/native_picker.dart';
import '../../../app/ux_copy.dart';
import '../../../app/readiness_controller.dart';
import '../../../widgets/desktop_page_scaffold.dart';
import '../../settings/controllers/component_settings_controller.dart';
import '../../settings/presentation/component_settings_pane.dart';

/// Navigation adapter over the existing host editor; not a workspace registry.
class WorkspacePage extends StatefulWidget {
  const WorkspacePage(
      {super.key,
      required this.backend,
      this.componentController,
      this.readiness,
      this.connected = false,
      this.root = '',
      this.picker = const UnavailablePicker()});
  final SetupBackend backend;
  final ReadinessController? readiness;
  final bool connected;
  final ComponentSettingsController? componentController;
  final String root;
  final NativePicker picker;
  @override
  State<WorkspacePage> createState() => _WorkspacePageState();
}

class _WorkspacePageState extends State<WorkspacePage> {
  late final ComponentSettingsController controller;
  @override
  void initState() {
    super.initState();
    controller = widget.componentController ??
        ComponentSettingsController(widget.backend, root: widget.root);
  }

  @override
  void dispose() {
    if (widget.componentController == null) controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => DesktopPageScaffold(
      title: 'Workspace configuration',
      description: DesktopCopy.workspace,
      maxWidth: 760,
      child: ListenableBuilder(
          listenable: Listenable.merge(
              [controller, if (widget.readiness != null) widget.readiness!]),
          builder: (context, _) =>
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                if (widget.readiness != null) ...[
                  SelectableText(
                      'Running workspace: ${widget.connected ? widget.readiness!.current?.configuration?.workspace ?? 'Unknown' : 'Unknown · disconnected'}'),
                  Text(widget.readiness!.label('host', controller.snapshot,
                      connected: widget.connected)),
                  const SizedBox(height: 16),
                ],
                ComponentSettingsPane(
                    controller: controller,
                    section: 'host',
                    picker: widget.picker),
              ])));
}
