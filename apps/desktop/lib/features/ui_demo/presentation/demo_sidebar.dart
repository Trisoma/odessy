import 'package:flutter/material.dart';

import '../../../widgets/pixel_boat.dart';
import 'demo_style.dart';

class DemoSidebar extends StatefulWidget {
  const DemoSidebar({
    super.key,
    required this.session,
    required this.onSession,
    required this.onNew,
    required this.onClose,
    required this.destination,
    required this.onDestination,
    this.drawer = false,
  });
  final int session;
  final ValueChanged<int> onSession;
  final VoidCallback onNew;
  final VoidCallback onClose;
  final bool drawer;
  final DemoDestination destination;
  final ValueChanged<DemoDestination> onDestination;

  @override
  State<DemoSidebar> createState() => _DemoSidebarState();
}

class _DemoSidebarState extends State<DemoSidebar> {
  bool expanded = true;
  String query = '';
  static const sessions = [
    'Review collector reconnect',
    'Understand the workspace',
    'Trace permission boundaries',
  ];

  Widget entry(IconData icon, String text,
          {bool selected = false, VoidCallback? onTap, Widget? trailing}) =>
      Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Material(
          color: selected ? DemoColors.raised : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
          child: ListTile(
            dense: true,
            minLeadingWidth: 18,
            horizontalTitleGap: 10,
            contentPadding: const EdgeInsets.symmetric(horizontal: 12),
            shape:
                RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            leading: Icon(icon,
                size: 18, color: selected ? DemoColors.mint : DemoColors.muted),
            title: Text(text,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontSize: 13,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w400)),
            selected: selected,
            selectedColor: DemoColors.ink,
            trailing: trailing,
            onTap: onTap,
          ),
        ),
      );

  @override
  Widget build(BuildContext context) => Material(
        color: DemoColors.sidebar,
        child: Column(children: [
          SizedBox(
              height: 74,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Row(children: [
                  const PixelBoat(size: 30),
                  const SizedBox(width: 10),
                  const Text('Odessy',
                      style: TextStyle(
                          fontSize: 23,
                          letterSpacing: -.7,
                          fontWeight: FontWeight.w700)),
                  const Spacer(),
                  IconButton(
                    tooltip:
                        widget.drawer ? 'Close navigation' : 'Collapse sidebar',
                    onPressed: widget.onClose,
                    icon: Icon(
                        widget.drawer
                            ? Icons.close
                            : Icons.vertical_split_outlined,
                        size: 19),
                  ),
                ]),
              )),
          Expanded(
              child: ListView(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                SizedBox(
                  height: 42,
                  child: TextField(
                    onChanged: (value) =>
                        setState(() => query = value.toLowerCase()),
                    decoration: const InputDecoration(
                        hintText: 'Find a session',
                        prefixIcon: Icon(Icons.search, size: 18),
                        contentPadding: EdgeInsets.symmetric(horizontal: 10)),
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                    onPressed: widget.onNew,
                    icon: const Icon(Icons.add, size: 18),
                    label: const Text('New session')),
                const SizedBox(height: 16),
                entry(Icons.chat_bubble_outline, 'Assistant',
                    selected: widget.destination == DemoDestination.assistant,
                    onTap: () =>
                        widget.onDestination(DemoDestination.assistant)),
                entry(Icons.workspaces_outline, 'Workspaces',
                    selected: widget.destination == DemoDestination.workspaces,
                    onTap: () =>
                        widget.onDestination(DemoDestination.workspaces),
                    trailing: const Text('1',
                        style: TextStyle(color: DemoColors.muted))),
                entry(Icons.devices_outlined, 'Devices',
                    selected: widget.destination == DemoDestination.devices,
                    onTap: () => widget.onDestination(DemoDestination.devices),
                    trailing: const Text('2',
                        style: TextStyle(color: DemoColors.muted))),
                entry(Icons.insights_outlined, 'Activity & usage',
                    selected:
                        widget.destination == DemoDestination.activityUsage,
                    onTap: () =>
                        widget.onDestination(DemoDestination.activityUsage)),
                entry(Icons.work_outline, 'Jobs',
                    selected: widget.destination == DemoDestination.jobs,
                    onTap: () => widget.onDestination(DemoDestination.jobs)),
                entry(Icons.swap_horiz, 'Handoffs',
                    selected: widget.destination == DemoDestination.handoffs,
                    onTap: () =>
                        widget.onDestination(DemoDestination.handoffs)),
                DemoSectionLabel('Projects',
                    trailing: IconButton(
                        tooltip: 'About demo projects',
                        onPressed: () => demoNotice(context,
                            'Projects are in-memory fixtures. No folders are opened.'),
                        icon: const Icon(Icons.create_new_folder_outlined,
                            size: 17))),
                entry(Icons.folder_open, 'odessy',
                    selected: expanded,
                    trailing: Icon(
                        expanded ? Icons.expand_less : Icons.expand_more,
                        size: 18),
                    onTap: () => setState(() => expanded = !expanded)),
                if (expanded) ...[
                  const Padding(
                    padding: EdgeInsets.fromLTRB(42, 3, 8, 8),
                    child: Text('This computer · demo workspace',
                        style:
                            TextStyle(fontSize: 11, color: DemoColors.muted)),
                  ),
                  for (var i = 0; i < sessions.length; i++)
                    if (sessions[i].toLowerCase().contains(query))
                      Padding(
                        padding: const EdgeInsets.only(left: 14),
                        child: entry(
                            i == 0
                                ? Icons.rate_review_outlined
                                : Icons.chat_outlined,
                            sessions[i],
                            selected: widget.session == i &&
                                widget.destination == DemoDestination.assistant,
                            onTap: () => widget.onSession(i)),
                      ),
                  if (!sessions.any((s) => s.toLowerCase().contains(query)))
                    const Padding(
                        padding: EdgeInsets.all(16),
                        child: Text('No matching sessions')),
                ],
                const DemoSectionLabel('Earlier'),
                entry(Icons.folder_outlined, 'Research notes',
                    onTap: () => demoNotice(
                        context, 'Research notes is a placeholder project.')),
              ])),
          Container(
            margin: const EdgeInsets.all(16),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
                color: DemoColors.background,
                border: Border.all(color: DemoColors.line),
                borderRadius: BorderRadius.circular(10)),
            child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Computers · fixtures',
                      style:
                          TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
                  SizedBox(height: 12),
                  Row(children: [
                    Icon(Icons.laptop_windows,
                        size: 17, color: DemoColors.mint),
                    SizedBox(width: 8),
                    Text('This computer', style: TextStyle(fontSize: 12))
                  ]),
                  SizedBox(height: 10),
                  Row(children: [
                    Icon(Icons.memory, size: 17, color: DemoColors.blue),
                    SizedBox(width: 8),
                    Text('Jetson · SSH', style: TextStyle(fontSize: 12))
                  ]),
                  Padding(
                      padding: EdgeInsets.only(left: 25, top: 3),
                      child: Text('Monitoring only · sample, 2s ago',
                          style: TextStyle(
                              fontSize: 10, color: DemoColors.muted))),
                ]),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: entry(Icons.tune, 'Settings',
                selected: widget.destination == DemoDestination.settings,
                onTap: () => widget.onDestination(DemoDestination.settings),
                trailing: PopupMenuButton<DemoDestination>(
                    tooltip: 'Tools and recovery',
                    onSelected: widget.onDestination,
                    itemBuilder: (_) => const [
                          PopupMenuItem(
                              value: DemoDestination.installation,
                              child: Text('Installation')),
                          PopupMenuItem(
                              value: DemoDestination.maintenance,
                              child: Text('Runtime maintenance')),
                          PopupMenuItem(
                              value: DemoDestination.pairing,
                              child: Text('Local pairing tools')),
                        ],
                    icon: const Icon(Icons.more_horiz, size: 18))),
          ),
        ]),
      );
}
