import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../../app/session_controller.dart';
import '../../../app/theme.dart';
import '../../conversation/presentation/usage_history_dashboard.dart';
import '../../jobs/controllers/jobs_controller.dart';
import '../../jobs/presentation/jobs_page.dart';
import 'demo_handoffs.dart';
import '../../../widgets/pixel_boat.dart';
import 'demo_settings.dart';
import 'demo_devices.dart';
import 'demo_inspector.dart';
import 'demo_sidebar.dart';
import 'demo_style.dart';

/// Opt-in, in-memory presentation experiment; no API, disk or device access.
class TastecodeDemo extends StatelessWidget {
  const TastecodeDemo({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Odessy · workspace design demo',
        debugShowCheckedModeBanner: false,
        theme: odessyTheme(Brightness.dark).copyWith(
          scaffoldBackgroundColor: DemoColors.background,
          colorScheme: const ColorScheme.dark(
              primary: DemoColors.mint,
              onPrimary: DemoColors.background,
              secondary: DemoColors.blue,
              surface: DemoColors.surface,
              onSurface: DemoColors.ink),
          dividerColor: DemoColors.line,
          inputDecorationTheme: InputDecorationTheme(
              filled: true,
              fillColor: DemoColors.background,
              hintStyle: const TextStyle(color: DemoColors.muted, fontSize: 12),
              border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide.none),
              focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide:
                      const BorderSide(color: DemoColors.mint, width: 2))),
          textButtonTheme: TextButtonThemeData(
              style: TextButton.styleFrom(
                  foregroundColor: DemoColors.muted,
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(7)))),
          filledButtonTheme: FilledButtonThemeData(
              style: FilledButton.styleFrom(
                  backgroundColor: DemoColors.mint,
                  foregroundColor: DemoColors.background,
                  minimumSize: const Size(44, 42),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8)))),
          iconButtonTheme: IconButtonThemeData(
              style: IconButton.styleFrom(
                  foregroundColor: DemoColors.muted,
                  minimumSize: const Size(40, 40))),
        ),
        home: const DemoWorkspace(),
      );
}

class DemoWorkspace extends StatefulWidget {
  const DemoWorkspace({super.key});

  @override
  State<DemoWorkspace> createState() => _DemoWorkspaceState();
}

class _DemoWorkspaceState extends State<DemoWorkspace> {
  // Explicit offline fixture transport; never a normal app connection.
  late final DesktopSession retainedSession;
  late final JobsController retainedJobs;
  @override
  void initState() {
    super.initState();
    retainedSession = DesktopSession(UsagePreviewApi());
    retainedJobs = JobsController(retainedSession);
    retainedSession.start();
  }

  final scaffoldKey = GlobalKey<ScaffoldState>();
  final input = TextEditingController();
  final composerFocus = FocusNode(debugLabel: 'Demo composer');
  final transcript = ScrollController();
  DemoPanel panel = DemoPanel.files;
  DemoDestination destination = DemoDestination.assistant;
  bool sidebar = true;
  bool inspector = true;
  bool activity = false;
  bool voice = false;
  bool attached = false;
  bool busy = false;
  int session = 0;
  String decision = '';
  String model = 'Qwen3 / Custom';
  String policy = 'Ask before actions';
  final List<String> messages = [];

  static const titles = [
    'Review collector reconnect',
    'Understand the workspace',
    'Trace permission boundaries',
  ];
  static const requests = [
    'Review the collector reconnect patch. Keep SSH monitoring-only, and show me what would change.',
    'Show me how this workspace is organized before we make any changes.',
    'Explain which permissions are needed for monitoring and for execution.',
  ];
  static const responses = [
    'The reconnect path should close its previous reader before opening another. '
        'I have a small proposed patch ready for review; execution and workspace capabilities stay disabled for SSH.',
    'The fixture workspace has a collector, a focused regression example, and a short README. '
        'Open the files beside this conversation to explore them without losing your place.',
    'SSH authentication establishes a connection. Monitoring permission controls telemetry access. '
        'Neither grants execution or workspace access. The fixture patch keeps those capabilities false.',
  ];

  @override
  void dispose() {
    retainedJobs.dispose();
    retainedSession.dispose();
    input.dispose();
    composerFocus.dispose();
    transcript.dispose();
    super.dispose();
  }

  void selectSession(int value) {
    setState(() {
      destination = DemoDestination.assistant;
      session = value;
      messages.clear();
      decision = '';
      busy = false;
      activity = false;
      input.clear();
      attached = false;
      voice = false;
    });
    if (scaffoldKey.currentState?.isDrawerOpen ?? false) {
      scaffoldKey.currentState!.closeDrawer();
    }
    if (transcript.hasClients) transcript.jumpTo(0);
  }

  void submit() {
    if (busy || input.text.trim().isEmpty) return;
    setState(() {
      messages.add(input.text.trim());
      input.clear();
      busy = true;
      attached = false;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && transcript.hasClients) {
        transcript.animateTo(transcript.position.maxScrollExtent,
            duration: const Duration(milliseconds: 180), curve: Curves.easeOut);
      }
    });
  }

  void openPanel(DemoPanel next, bool wide) {
    setState(() {
      panel = next;
      inspector = true;
    });
    if (!wide) scaffoldKey.currentState!.openEndDrawer();
  }

  void navigate(DemoDestination value) {
    setState(() {
      destination = value;
      if (value != DemoDestination.assistant) voice = false;
    });
    scaffoldKey.currentState?.closeDrawer();
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, size) {
        final wide = size.maxWidth >= 1240;
        final fullSidebar = size.maxWidth >= 900 && sidebar;
        Widget navigation(bool drawer) => DemoSidebar(
            drawer: drawer,
            destination: destination,
            onDestination: navigate,
            session: session,
            onSession: selectSession,
            onNew: () => selectSession(-1),
            onClose: () {
              if (drawer) {
                scaffoldKey.currentState!.closeDrawer();
              } else {
                setState(() => sidebar = false);
              }
            });
        Widget workspacePanel(bool drawer) => DemoInspector(
            panel: panel,
            onPanel: (value) => setState(() => panel = value),
            onClose: () {
              if (drawer) {
                scaffoldKey.currentState!.closeEndDrawer();
              } else {
                setState(() => inspector = false);
              }
            });
        return Scaffold(
          key: scaffoldKey,
          drawer: Drawer(width: 286, child: navigation(true)),
          endDrawer: Drawer(
              width: size.maxWidth < 560 ? size.maxWidth - 24 : 500,
              child: workspacePanel(true)),
          body: SafeArea(
              child: Column(children: [
            Container(
              width: double.infinity,
              color: const Color(0xff294238),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
              child: const Text(
                  'DESIGN DEMO  /  Fixtures only · no services, files, microphone or commands connected',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 11, color: DemoColors.mint)),
            ),
            Expanded(
                child: Row(children: [
              if (fullSidebar) ...[
                SizedBox(width: 264, child: navigation(false)),
                const VerticalDivider(width: 1),
              ],
              Expanded(
                  child: Column(children: [
                _header(fullSidebar, wide),
                const Divider(height: 1),
                Expanded(
                    child: switch (destination) {
                  DemoDestination.assistant => _conversation(wide),
                  DemoDestination.settings => const DemoSettings(),
                  DemoDestination.devices => const DemoDevices(),
                  DemoDestination.workspaces =>
                    DemoWorkspaces(onOpen: () => selectSession(0)),
                  DemoDestination.activityUsage =>
                    UsageHistoryDashboard(session: retainedSession),
                  DemoDestination.jobs => JobsPage(controller: retainedJobs),
                  DemoDestination.handoffs => const DemoHandoffs(),
                  DemoDestination.installation ||
                  DemoDestination.maintenance ||
                  DemoDestination.pairing =>
                    _retainedTools(),
                }),
                if (destination == DemoDestination.assistant) _composer(),
              ])),
              if (wide &&
                  inspector &&
                  destination == DemoDestination.assistant) ...[
                const VerticalDivider(width: 1),
                SizedBox(width: 390, child: workspacePanel(false)),
              ],
            ])),
          ])),
        );
      });

  Widget _header(bool fullSidebar, bool wide) => Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
        child: Row(children: [
          if (!fullSidebar) ...[
            IconButton(
                tooltip: 'Open navigation',
                icon: const Icon(Icons.menu),
                onPressed: () {
                  if (MediaQuery.sizeOf(context).width >= 900) {
                    setState(() => sidebar = true);
                  } else {
                    scaffoldKey.currentState!.openDrawer();
                  }
                }),
            const SizedBox(width: 4),
          ],
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text('odessy / ${destination.name}',
                    style:
                        const TextStyle(fontSize: 11, color: DemoColors.muted)),
                const SizedBox(height: 5),
                Text(
                    destination == DemoDestination.assistant
                        ? session < 0
                            ? 'A new starting point'
                            : titles[session]
                        : switch (destination) {
                            DemoDestination.workspaces => 'Your workspaces',
                            DemoDestination.devices =>
                              'Computers and monitoring',
                            DemoDestination.activityUsage => 'Activity & usage',
                            DemoDestination.jobs => 'Jobs',
                            DemoDestination.handoffs => 'Handoffs',
                            DemoDestination.installation => 'Installation',
                            DemoDestination.maintenance =>
                              'Runtime maintenance',
                            DemoDestination.pairing => 'Local pairing tools',
                            _ => 'Settings',
                          },
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 17, fontWeight: FontWeight.w700)),
              ])),
          const SizedBox(width: 8),
          if (MediaQuery.sizeOf(context).width > 700)
            const DemoTag('Local fixture',
                color: DemoColors.mint, icon: Icons.laptop_windows),
          const SizedBox(width: 4),
          if (destination == DemoDestination.assistant) ...[
            IconButton(
                tooltip: 'Open files',
                icon: const Icon(Icons.folder_open_outlined, size: 20),
                onPressed: () => openPanel(DemoPanel.files, wide)),
            IconButton(
                tooltip: 'Open terminal',
                icon: const Icon(Icons.terminal, size: 20),
                onPressed: () => openPanel(DemoPanel.terminal, wide)),
            IconButton(
                tooltip: 'Open changes',
                icon: const Icon(Icons.difference_outlined, size: 20),
                onPressed: () => openPanel(DemoPanel.changes, wide)),
          ],
        ]),
      );

  Widget _retainedTools() =>
      ListView(padding: const EdgeInsets.all(32), children: [
        const DemoTag(
            'Retained production destination · no operations in this demo',
            icon: Icons.info_outline),
        const SizedBox(height: 24),
        DemoCard(
            title: switch (destination) {
              DemoDestination.pairing => 'Local pairing tools',
              DemoDestination.maintenance => 'Runtime maintenance',
              _ => 'Installation and recovery',
            },
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(
                  switch (destination) {
                    DemoDestination.pairing =>
                      'Existing identity, pairing, grant and revoke flows remain available in production. SSH monitoring does not replace paired transport.',
                    DemoDestination.maintenance =>
                      'Existing runtime configuration, verification and recovery flows remain available in production. Active-work and consent guards remain in force.',
                    _ =>
                      'Existing install, repair, local-package update, rollback, configuration and external uninstall flows remain available in production.',
                  },
                  style: const TextStyle(height: 1.8)),
              const SizedBox(height: 20),
              const Text(
                  'This review demo opens no helper and changes no installation, trust or permission. These are preservation requirements, not new operational controls.'),
              const SizedBox(height: 20),
              OutlinedButton(
                  onPressed: () => navigate(DemoDestination.settings),
                  child: const Text('Review settings')),
            ])),
      ]);

  Widget _conversation(bool wide) =>
      LayoutBuilder(builder: (context, constraints) {
        final inset = constraints.maxWidth < 600 ? 18.0 : 32.0;
        return Align(
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: ListView(
              controller: transcript,
              padding: EdgeInsets.fromLTRB(inset, 28, inset, 24),
              children: session < 0 && messages.isEmpty
                  ? [
                      const SizedBox(height: 60),
                      const Icon(Icons.explore_outlined,
                          size: 42, color: DemoColors.mint),
                      const SizedBox(height: 20),
                      const Text('What would you like to explore?',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                              fontSize: 26, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 12),
                      const Text(
                          'Start with context. Keep every action reviewable.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: DemoColors.muted)),
                      const SizedBox(height: 28),
                      for (final suggestion in [
                        'Review the collector reconnect patch',
                        'Explain monitoring permissions'
                      ])
                        Padding(
                            padding: const EdgeInsets.only(bottom: 10),
                            child: OutlinedButton.icon(
                                onPressed: () {
                                  input.text = suggestion;
                                  composerFocus.requestFocus();
                                },
                                icon: const Icon(Icons.arrow_outward, size: 16),
                                label: Text(suggestion))),
                    ]
                  : [
                      if (session >= 0) ...[
                        const Center(
                            child: Text('Today · sample conversation',
                                style: TextStyle(
                                    fontSize: 11, color: DemoColors.muted))),
                        const SizedBox(height: 22),
                        _userMessage(requests[session]),
                        const SizedBox(height: 26),
                        Row(children: [
                          Container(
                              padding: const EdgeInsets.all(6),
                              decoration: BoxDecoration(
                                  color: DemoColors.mint.withValues(alpha: .12),
                                  borderRadius: BorderRadius.circular(7)),
                              child: const PixelBoat(size: 22)),
                          const SizedBox(width: 10),
                          const Text('Odessy',
                              style: TextStyle(fontWeight: FontWeight.w700)),
                          const SizedBox(width: 10),
                          const Text('Custom engine · fixture',
                              style: TextStyle(
                                  fontSize: 11, color: DemoColors.muted)),
                        ]),
                        const SizedBox(height: 16),
                        SelectableText(responses[session],
                            style: const TextStyle(fontSize: 14, height: 1.85)),
                        const SizedBox(height: 20),
                        _activity(wide),
                        const SizedBox(height: 18),
                        if (session == 0) _approval(wide),
                        if (session != 0)
                          const DemoTag(
                              'Read-only explanation · no action requested',
                              color: DemoColors.blue),
                        const SizedBox(height: 16),
                        const Text('Sample response · no model was called',
                            style: TextStyle(
                                fontSize: 11, color: DemoColors.muted)),
                      ],
                      for (final message in messages) ...[
                        const SizedBox(height: 24),
                        _userMessage(message),
                      ],
                      if (messages.isNotEmpty) ...[
                        const SizedBox(height: 18),
                        DemoTag(
                            busy
                                ? 'Simulated working state · use Stop to end it'
                                : 'Simulation stopped · no action was executed',
                            color: busy ? DemoColors.amber : DemoColors.muted,
                            icon: busy
                                ? Icons.pending_outlined
                                : Icons.stop_circle_outlined),
                      ],
                    ],
            ),
          ),
        );
      });

  Widget _userMessage(String text) => Align(
        alignment: Alignment.centerRight,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 620),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
              color: DemoColors.raised,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: DemoColors.line)),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('You',
                style: TextStyle(
                    fontSize: 11,
                    color: DemoColors.blue,
                    fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            SelectableText(text,
                style: const TextStyle(fontSize: 14, height: 1.7)),
          ]),
        ),
      );

  Widget _activity(bool wide) => Container(
        decoration: BoxDecoration(
            border: Border.all(color: DemoColors.line),
            borderRadius: BorderRadius.circular(10)),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          InkWell(
            key: const ValueKey('activity-toggle'),
            borderRadius: BorderRadius.circular(10),
            onTap: () => setState(() => activity = !activity),
            child: Semantics(
              button: true,
              expanded: activity,
              child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Row(children: [
                    const Icon(Icons.account_tree_outlined,
                        size: 18, color: DemoColors.blue),
                    const SizedBox(width: 10),
                    const Expanded(
                        child: Text('Review activity · 3 steps',
                            style: TextStyle(
                                fontSize: 12, fontWeight: FontWeight.w700))),
                    Icon(activity ? Icons.expand_less : Icons.expand_more,
                        size: 18),
                  ])),
            ),
          ),
          if (activity) ...[
            const Divider(height: 1),
            for (final (icon, title, detail, color) in [
              (
                Icons.check_circle_outline,
                'Read sample collector',
                'Fixture · collector.py · 15 lines',
                DemoColors.mint
              ),
              (
                Icons.check_circle_outline,
                'Compare reconnect behavior',
                'Fixture · close reader before reconnect',
                DemoColors.mint
              ),
              (
                Icons.error_outline,
                'Live verification unavailable',
                'Demo has no collector or remote connection',
                DemoColors.amber
              ),
            ])
              Padding(
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(icon, size: 17, color: color),
                        const SizedBox(width: 10),
                        Expanded(
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                              Text(title,
                                  style: const TextStyle(
                                      fontSize: 12,
                                      fontWeight: FontWeight.w700)),
                              const SizedBox(height: 3),
                              Text(detail,
                                  style: const TextStyle(
                                      fontSize: 11, color: DemoColors.muted)),
                            ])),
                      ])),
            Padding(
                padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
                child: Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(
                        onPressed: () => openPanel(DemoPanel.terminal, wide),
                        icon: const Icon(Icons.terminal, size: 16),
                        label: const Text('View illustrative output')))),
          ],
        ]),
      );

  Widget _approval(bool wide) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
            color: const Color(0xff30342d),
            border: Border.all(color: const Color(0xff827454)),
            borderRadius: BorderRadius.circular(10)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.shield_outlined,
                size: 19, color: DemoColors.amber),
            const SizedBox(width: 9),
            Expanded(
                child: Text(
                    decision.isEmpty
                        ? 'Your review is needed'
                        : 'Demo decision recorded',
                    style: const TextStyle(
                        color: DemoColors.amber,
                        fontWeight: FontWeight.w700,
                        fontSize: 13)))
          ]),
          const SizedBox(height: 10),
          const Text('Proposed edit · sample/collector.py',
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
          const SizedBox(height: 7),
          const Text(
              'Close the previous reader, then reconnect. Local fixture only; no remote changes.',
              style:
                  TextStyle(fontSize: 12, height: 1.7, color: DemoColors.ink)),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            TextButton.icon(
                onPressed: () => openPanel(DemoPanel.changes, wide),
                icon: const Icon(Icons.difference_outlined, size: 16),
                label: const Text('Inspect diff')),
            if (decision.isEmpty) ...[
              OutlinedButton(
                  onPressed: () => setState(() => decision = 'Denied'),
                  child: const Text('Deny demo')),
              FilledButton(
                  onPressed: () => setState(() => decision = 'Approved'),
                  child: const Text('Approve demo')),
            ] else
              DemoTag('$decision · nothing applied',
                  color: DemoColors.mint, icon: Icons.check_circle_outline),
          ]),
          const SizedBox(height: 8),
          const Text(
              'Simulation only. This grants no permissions and writes no files.',
              style: TextStyle(fontSize: 10, color: DemoColors.muted)),
        ]),
      );

  Widget _composer() => LayoutBuilder(builder: (context, constraints) {
        final compact = constraints.maxWidth < 600;
        return Center(
            child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 840),
          child: Padding(
            padding: EdgeInsets.fromLTRB(
                compact ? 16 : 28, 8, compact ? 16 : 28, 12),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Container(
                decoration: BoxDecoration(
                    color: DemoColors.surface,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: DemoColors.line),
                    boxShadow: [
                      BoxShadow(
                          color: Colors.black.withValues(alpha: .12),
                          blurRadius: 20,
                          offset: const Offset(0, 5))
                    ]),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 10),
                        decoration: const BoxDecoration(
                            border: Border(
                                bottom: BorderSide(color: DemoColors.line))),
                        child: Wrap(
                            spacing: 12,
                            runSpacing: 6,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              const Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Icon(Icons.folder_open,
                                        size: 15, color: DemoColors.blue),
                                    SizedBox(width: 6),
                                    Text('odessy',
                                        style: TextStyle(
                                            fontSize: 12,
                                            fontWeight: FontWeight.w700))
                                  ]),
                              const Text('This computer · fixture workspace',
                                  style: TextStyle(
                                      fontSize: 11, color: DemoColors.muted)),
                              if (!compact)
                                const Text('branch / demo',
                                    style: TextStyle(
                                        fontSize: 11, color: DemoColors.muted)),
                            ]),
                      ),
                      if (attached)
                        Padding(
                            padding: const EdgeInsets.fromLTRB(14, 8, 14, 0),
                            child: Align(
                                alignment: Alignment.centerLeft,
                                child: InputChip(
                                    label: const Text('collector.py · fixture',
                                        style: TextStyle(fontSize: 11)),
                                    onDeleted: () =>
                                        setState(() => attached = false)))),
                      Shortcuts(
                        shortcuts: const {
                          SingleActivator(LogicalKeyboardKey.enter,
                              control: true): ActivateIntent()
                        },
                        child: Actions(
                          actions: {
                            ActivateIntent:
                                CallbackAction<ActivateIntent>(onInvoke: (_) {
                              submit();
                              return null;
                            })
                          },
                          child: TextField(
                            key: const ValueKey('demo-composer'),
                            controller: input,
                            focusNode: composerFocus,
                            minLines: 2,
                            maxLines: 4,
                            style: const TextStyle(fontSize: 14, height: 1.7),
                            decoration: const InputDecoration(
                                hintText:
                                    'Ask a question, explore a file, or describe a change…',
                                filled: false,
                                contentPadding:
                                    EdgeInsets.fromLTRB(16, 14, 16, 10),
                                enabledBorder: InputBorder.none,
                                focusedBorder: OutlineInputBorder(
                                    borderSide:
                                        BorderSide(color: DemoColors.mint),
                                    borderRadius:
                                        BorderRadius.all(Radius.circular(8)))),
                          ),
                        ),
                      ),
                      Padding(
                          padding: const EdgeInsets.fromLTRB(8, 0, 10, 10),
                          child: Column(children: [
                            Row(children: [
                              IconButton(
                                  tooltip: 'Attach fixture file',
                                  icon: const Icon(Icons.attach_file, size: 19),
                                  onPressed: () =>
                                      setState(() => attached = !attached)),
                              Expanded(
                                  child: PopupMenuButton<String>(
                                tooltip: 'Choose demo review policy',
                                initialValue: policy,
                                onSelected: (value) =>
                                    setState(() => policy = value),
                                itemBuilder: (_) => [
                                  'Ask before actions',
                                  'Read-only preview'
                                ]
                                    .map((value) => PopupMenuItem(
                                        value: value, child: Text(value)))
                                    .toList(),
                                child: Padding(
                                  padding:
                                      const EdgeInsets.symmetric(vertical: 10),
                                  child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        const Icon(Icons.shield_outlined,
                                            size: 15, color: DemoColors.amber),
                                        const SizedBox(width: 5),
                                        Flexible(
                                            child: Text(policy,
                                                overflow: TextOverflow.ellipsis,
                                                style: const TextStyle(
                                                    fontSize: 11,
                                                    color: DemoColors.amber))),
                                        const Icon(Icons.expand_more,
                                            size: 15, color: DemoColors.amber),
                                      ]),
                                ),
                              )),
                              if (!compact) ...[
                                _modelPicker(),
                                const SizedBox(width: 8)
                              ],
                              IconButton(
                                  tooltip: voice
                                      ? 'End simulated listening'
                                      : 'Simulate voice state (no microphone)',
                                  style: IconButton.styleFrom(
                                      foregroundColor: voice
                                          ? DemoColors.mint
                                          : DemoColors.muted,
                                      backgroundColor:
                                          voice ? DemoColors.raised : null),
                                  icon: Icon(
                                      voice ? Icons.graphic_eq : Icons.mic_none,
                                      size: 20),
                                  onPressed: () =>
                                      setState(() => voice = !voice)),
                              const SizedBox(width: 6),
                              FilledButton(
                                key: const ValueKey('send-stop'),
                                onPressed: busy
                                    ? () => setState(() => busy = false)
                                    : submit,
                                style: FilledButton.styleFrom(
                                    minimumSize: const Size(42, 42),
                                    padding: const EdgeInsets.all(10)),
                                child: Tooltip(
                                    message: busy
                                        ? 'Stop simulation'
                                        : 'Send demo message (Ctrl+Enter)',
                                    child: Icon(
                                        busy
                                            ? Icons.stop_rounded
                                            : Icons.arrow_upward,
                                        size: 21)),
                              ),
                            ]),
                            if (compact)
                              Align(
                                  alignment: Alignment.centerLeft,
                                  child: _modelPicker()),
                          ])),
                    ]),
              ),
              const SizedBox(height: 8),
              Text(
                  voice
                      ? 'Simulated listening · microphone is not accessed'
                      : 'Voice off · demo only  /  Ctrl+Enter to send · Enter for a new line',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                      fontSize: 10,
                      color: voice ? DemoColors.mint : DemoColors.muted)),
            ]),
          ),
        ));
      });

  Widget _modelPicker() => PopupMenuButton<String>(
        tooltip: 'Choose demo model and engine (next turn)',
        initialValue: model,
        onSelected: (value) => setState(() => model = value),
        itemBuilder: (_) => [
          'Qwen3 / Custom',
          'Qwen3 / Harness',
          'Cloud model / Harness'
        ]
            .map((value) =>
                PopupMenuItem(value: value, child: Text('$value · fixture')))
            .toList(),
        child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.tune, size: 14, color: DemoColors.blue),
              const SizedBox(width: 6),
              Text(model,
                  style: const TextStyle(fontSize: 11, color: DemoColors.ink)),
              const Icon(Icons.expand_more, size: 15),
            ])),
      );
}
