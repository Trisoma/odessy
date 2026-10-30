import 'dart:async';
import 'dart:io';
import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../features/conversation/presentation/conversation.dart';
import '../features/devices/presentation/devices.dart';
import '../features/devices/presentation/monitoring_details.dart';
import '../features/devices/presentation/monitoring_history.dart';
import '../features/devices/controllers/device_monitoring_controller.dart';
import '../features/shell/presentation/device_sidebar.dart';
import '../features/settings/presentation/settings.dart';
import '../features/jobs/controllers/jobs_controller.dart';
import '../features/jobs/presentation/jobs_page.dart';
import 'session_controller.dart';
import 'theme.dart';
import 'connections/connection_dialog.dart';
import 'connections/windows_credential_store.dart';
import '../widgets/glass_surface.dart';
import '../features/setup/controllers/setup_controller.dart';
import '../features/setup/presentation/setup_wizard.dart';
import 'setup/local_setup_backend.dart';
import '../features/setup/controllers/maintenance_controller.dart';
import '../features/setup/controllers/installed_manage_controller.dart';
import '../features/setup/presentation/maintenance_page.dart';
import '../features/setup/presentation/manage_page.dart';
import '../features/setup/presentation/get_started_page.dart';
import 'setup/installation_location.dart';
import 'setup/disconnected_api.dart';
import 'setup/native_picker.dart';

class OdessyApp extends StatefulWidget {
  const OdessyApp(
      {super.key,
      this.api,
      this.installed,
      this.localDesktop = false,
      this.managementBackend,
      this.onConfigured,
      this.onExitApproved});
  final VoidCallback? onConfigured;
  final Future<void> Function()? onExitApproved;
  final OdessyApi? api;
  final bool localDesktop;

  /// Optional borrowed backend; its owner manages its lifetime.
  final SetupBackend? managementBackend;

  /// Recorded installed state when this process was started by the Windows
  /// launcher; null when using a separately configured local service.
  final Json? installed;
  @override
  State<OdessyApp> createState() => _OdessyAppState();
}

class _OdessyAppState extends State<OdessyApp> {
  late final DesktopSession controller;
  late final DeviceMonitoringController monitoring;
  late final JobsController jobs;
  late final SetupController setup;
  late final MaintenanceController maintenance, pairing, installerEngine;
  InstalledManageController? installManagement;
  final CredentialStore credentials = MemoryCredentialStore();
  final CredentialStore? credentialVault =
      Platform.isWindows ? WindowsCredentialStore() : null;
  late final AppLifecycleListener lifecycle;
  final settingsLeaveGuard = SettingsLeaveGuard();
  bool _departurePending = false, _closing = false;
  bool _showModelHint = true;
  Future<AppExitResponse>? _exitDecision;
  ThemeMode mode = ThemeMode.dark;
  String page = 'Conversation';
  @override
  void initState() {
    super.initState();
    controller = DesktopSession(widget.api ?? DisconnectedApi());
    jobs = JobsController(controller);
    monitoring = DeviceMonitoringController();
    controller.addListener(_syncMonitoring);
    monitoring.bind(controller.api);
    setup = SetupController(LocalSetupBackend());
    maintenance = MaintenanceController(LocalSetupBackend());
    installerEngine = MaintenanceController(LocalSetupBackend());
    pairing = MaintenanceController(LocalSetupBackend());
    if (widget.localDesktop) {
      const root = String.fromEnvironment('ODESSY_DESKTOP_ROOT');
      final link = '$root/build/odessy-link/odessy-link.exe';
      if (root.isNotEmpty && File(link).existsSync()) {
        pairing.update({'link': link});
      }
    }
    final installed = widget.installed;
    if (installed != null) {
      installManagement = InstalledManageController(
          widget.managementBackend ?? LocalSetupBackend(),
          spec: {...installed, 'root': installed['root']})
        ..load();
    }
    lifecycle = AppLifecycleListener(onExitRequested: _requestExit);
    controller.start();
  }

  @override
  void dispose() {
    lifecycle.dispose();
    controller.removeListener(_syncMonitoring);
    jobs.dispose();
    monitoring.dispose();
    controller.dispose();
    setup.dispose();
    maintenance.dispose();
    installerEngine.dispose();
    pairing.dispose();
    installManagement?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
      title: 'Odessy',
      debugShowCheckedModeBanner: false,
      theme: odessyTheme(Brightness.light),
      darkTheme: odessyTheme(Brightness.dark),
      themeMode: mode,
      home: ListenableBuilder(
          listenable: Listenable.merge([controller, monitoring]),
          builder: (context, _) =>
              LayoutBuilder(builder: (context, constraints) {
                final narrow = constraints.maxWidth < 900;
                final sidebar = _sidebar(context);
                return Scaffold(
                    drawer: narrow ? Drawer(child: sidebar) : null,
                    appBar: narrow ? AppBar(title: const Text('Odessy')) : null,
                    body: Row(children: [
                      if (!narrow)
                        SizedBox(
                            width: 236,
                            child: GlassSurface(radius: 0, child: sidebar)),
                      if (!narrow) const VerticalDivider(width: 1),
                      Expanded(
                          child: Column(children: [
                        Padding(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 30, vertical: 18),
                            child: Row(children: [
                              Text(page,
                                  style:
                                      Theme.of(context).textTheme.titleMedium),
                              const Spacer(),
                              _badge(
                                  context,
                                  controller.api is DisconnectedApi
                                      ? 'NOT CONFIGURED'
                                      : controller.api.isDemo
                                          ? 'DEMO'
                                          : 'LOCAL API',
                                  controller.api.isDemo
                                      ? Colors.amber
                                      : controller.connected
                                          ? Colors.green
                                          : Colors.grey),
                              const SizedBox(width: 10),
                              if (widget.localDesktop)
                                IconButton(
                                  tooltip: 'Start new session',
                                  icon: const Icon(Icons.add_comment_outlined,
                                      size: 20),
                                  onPressed: controller.connected &&
                                          !controller.busy &&
                                          controller.session?.running != true
                                      ? () => _newSession(context)
                                      : null,
                                ),
                              IconButton(
                                  tooltip: 'Connect local service',
                                  icon: const Icon(Icons.cable_outlined,
                                      size: 20),
                                  onPressed: controller.session?.running == true
                                      ? null
                                      : () => _connect(context)),
                              IconButton(
                                  tooltip: 'Toggle light / dark theme',
                                  icon: Icon(
                                      mode == ThemeMode.dark
                                          ? Icons.light_mode_outlined
                                          : Icons.dark_mode_outlined,
                                      size: 20),
                                  onPressed: () => setState(() => mode =
                                      mode == ThemeMode.dark
                                          ? ThemeMode.light
                                          : ThemeMode.dark)),
                            ])),
                        const Divider(height: 1),
                        if (widget.localDesktop &&
                            _showModelHint &&
                            identical(controller.api, widget.api))
                          MaterialBanner(
                              content: const Text(
                                  'Add a model profile in Settings, start a new session, and select it in Conversation.'),
                              actions: [
                                TextButton(
                                    onPressed: () => _navigate('Settings'),
                                    child: const Text('Open settings')),
                                TextButton(
                                    onPressed: () =>
                                        setState(() => _showModelHint = false),
                                    child: const Text('Dismiss')),
                              ]),
                        if (controller.api is DisconnectedApi)
                          MaterialBanner(
                              content: Text(installManagement == null
                                  ? 'Connect to a local service to use Odessy.'
                                  : 'Model not configured — configure a model to enable chat. No model is running.'),
                              actions: [
                                TextButton(
                                    onPressed: installManagement == null
                                        ? () => _connect(context)
                                        : () => _navigate('Get started'),
                                    child: Text(installManagement == null
                                        ? 'Connect'
                                        : 'Configure model')),
                              ]),
                        if (controller.error.isNotEmpty)
                          MaterialBanner(
                              content: Text(controller.error),
                              actions: [
                                TextButton(
                                    onPressed: controller.refresh,
                                    child: const Text('Refresh'))
                              ]),
                        Expanded(
                            child: switch (page) {
                          'Setup' => SetupWizardPage(controller: setup),
                          'Manage installation' => ManagePage(
                              controller: installManagement!,
                              external: runningOutsideInstallation(
                                  widget.installed?['root'] as String? ?? ''),
                              onOpenMaintenance: () =>
                                  installManagement!.openMaintenance()),
                          'Get started' => GetStartedPage(
                              controller: installManagement!,
                              picker: const FileSelectorPicker(),
                              onStart: widget.onConfigured ??
                                  () => setState(() => page = 'Conversation'),
                              onClose: () =>
                                  setState(() => page = 'Conversation')),
                          'Voice setup' => GetStartedPage(
                              controller: installManagement!,
                              picker: const FileSelectorPicker(),
                              initialVoice: true,
                              onStart: widget.onConfigured ?? () {},
                              onClose: () =>
                                  setState(() => page = 'Conversation')),
                          'Installer' => MaintenancePage(
                              key: const ValueKey('installer-engine'),
                              controller: installerEngine,
                              installer: true),
                          'Maintenance' =>
                            MaintenancePage(controller: maintenance),
                          'Pairing' => MaintenancePage(
                              key: const ValueKey('pairing'),
                              controller: pairing,
                              pairing: true),
                          'Devices' => _devicePage(),
                          'Jobs' => JobsPage(controller: jobs),
                          'Settings' => SettingsPage(
                              key: ValueKey(controller.api),
                              controller: controller,
                              installed: widget.installed != null,
                              modelConfigured:
                                  widget.installed?['model_configured'] == true,
                              voiceEnabled:
                                  widget.installed?['voice_enabled'] == true,
                              themeMode: mode,
                              onToggleTheme: () => setState(() => mode =
                                  mode == ThemeMode.dark
                                      ? ThemeMode.light
                                      : ThemeMode.dark),
                              leaveGuard: settingsLeaveGuard,
                              onOpenDevices: () => _navigate('Devices'),
                              onOpenGetStarted: installManagement == null
                                  ? null
                                  : () => _navigate('Get started'),
                              onOpenVoice: installManagement == null
                                  ? null
                                  : () => _navigate('Voice setup'),
                              onOpenManagement: widget.installed == null
                                  ? null
                                  : () => _navigate('Manage installation')),
                          _ => ConversationPage(controller: controller),
                        }),
                      ])),
                    ]));
              })));
  void _syncMonitoring() => monitoring.bind(controller.api);

  Widget _sidebar(BuildContext context) => DeviceSidebar(
          hosts: monitoring.hosts,
          selectedHostId: page == 'Devices' ? monitoring.selectedId : null,
          conversationSelected: page == 'Conversation',
          settingsSelected: page == 'Settings',
          loading: monitoring.loadingInventory,
          retained: monitoring.inventoryRetained,
          error: monitoring.inventoryError,
          demo: monitoring.demo,
          connectionLabel: controller.api.isDemo
              ? 'Demo · no tools execute'
              : controller.api.identity,
          onConversation: () => _navigate('Conversation'),
          onSettings: () => _navigate('Settings'),
          onInspect: (id) => _navigate('Devices', hostId: id),
          onRefresh: () => unawaited(monitoring.refresh()),
          onPair: () => _navigate('Pairing'),
          destinations: [
            SidebarDestination(
                label: 'Jobs',
                icon: Icons.work_outline,
                selected: page == 'Jobs',
                onTap: () => _navigate('Jobs')),
            if (widget.installed != null) ...[
              SidebarDestination(
                  label: 'Manage installation',
                  icon: Icons.settings_outlined,
                  selected: page == 'Manage installation',
                  onTap: () => _navigate('Manage installation')),
              if (widget.installed?['model_configured'] != true)
                SidebarDestination(
                    label: 'Get started',
                    icon: Icons.rocket_launch_outlined,
                    selected: page == 'Get started',
                    onTap: () => _navigate('Get started')),
            ],
            for (final item in [
              ('Set up this computer', 'Setup', Icons.build_outlined),
              ('Install Odessy', 'Installer', Icons.download_outlined),
              ('Runtime maintenance', 'Maintenance', Icons.handyman_outlined)
            ])
              SidebarDestination(
                  label: item.$1,
                  icon: item.$3,
                  selected: page == item.$2,
                  onTap: controller.session?.running == true
                      ? null
                      : () => _navigate(item.$2)),
          ]);

  Widget _devicePage() => DevicePage(
      key: ValueKey(monitoring.selectedId),
      device: monitoring.selectedHost?.name ?? '',
      controller: controller,
      onManagePairing: () => _navigate('Pairing'),
      monitoringOverview: MonitoringDetails(
          showHeader: false,
          host: monitoring.inspectedHost,
          report: monitoring.metrics,
          loading: monitoring.loadingMetrics || monitoring.loadingInventory,
          retained: monitoring.metricsRetained,
          error: monitoring.metricsError ?? monitoring.inventoryError,
          demo: monitoring.demo,
          onRefresh: () => unawaited(monitoring.refreshSelected())),
      monitoringHistory:
          Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DropdownButton<HistoryWindow>(
            key: const Key('monitoring-history-window'),
            isExpanded: true,
            value: monitoring.window,
            items: [
              for (final item in HistoryWindow.values)
                DropdownMenuItem(value: item, child: Text(item.label))
            ],
            onChanged: (value) {
              if (value != null) monitoring.setWindow(value);
            }),
        MonitoringHistory(
            host: monitoring.inspectedHost,
            report: monitoring.history,
            loading: monitoring.loadingHistory,
            retained: monitoring.historyRetained,
            error: monitoring.historyError,
            degradation: monitoring.degradation,
            demo: monitoring.demo,
            onRefresh: () => unawaited(monitoring.refreshHistory())),
      ]));

  Widget _badge(BuildContext context, String text, Color color) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(6),
          color: color.withValues(alpha: .1)),
      child: Text(text,
          style: TextStyle(
              color: color,
              fontSize: 9,
              fontWeight: FontWeight.w600,
              letterSpacing: 1)));

  bool _requiresIdle(String destination) =>
      const {'Setup', 'Installer', 'Maintenance'}.contains(destination);

  Future<void> _navigate(String destination, {String? hostId}) async {
    if (_closing ||
        _departurePending ||
        (destination == page && hostId == null)) {
      return;
    }
    _departurePending = true;
    try {
      if (!await settingsLeaveGuard.requestLeave() || !mounted || _closing) {
        return;
      }
      if (_requiresIdle(destination) && controller.session?.running == true) {
        return;
      }
      if (hostId != null) {
        if (!monitoring.hosts.any((host) => host.id == hostId)) return;
        monitoring.select(hostId);
      }
      setState(() => page = destination);
    } finally {
      _departurePending = false;
    }
  }

  Future<AppExitResponse> _requestExit() =>
      _exitDecision ??= _exit().whenComplete(() => _exitDecision = null);

  Future<AppExitResponse> _exit() async {
    if (_closing) return AppExitResponse.exit;
    if (_departurePending) return AppExitResponse.cancel;
    _departurePending = true;
    try {
      if (!await settingsLeaveGuard.requestLeave() || !mounted) {
        return AppExitResponse.cancel;
      }
      _closing = true;
      await monitoring.shutdown();
      await controller.shutdown();
      await setup.shutdown();
      await maintenance.shutdown();
      await installerEngine.shutdown();
      await pairing.shutdown();
      installManagement?.dispose();
      installManagement = null;
      await widget.onExitApproved?.call();
      return AppExitResponse.exit;
    } finally {
      _departurePending = false;
    }
  }

  Future<void> _connect(BuildContext context) async {
    if (_closing || _departurePending || controller.session?.running == true) {
      return;
    }
    _departurePending = true;
    try {
      if (!await settingsLeaveGuard.requestLeave() ||
          !mounted ||
          !context.mounted ||
          controller.session?.running == true) {
        return;
      }
      final result = await showConnectionDialog(context, credentials,
          vault: credentialVault,
          allowDemo: controller.api.isDemo && !widget.localDesktop);
      if (!mounted || _closing) {
        result?.close();
        return;
      }
      if (result != null) await controller.replace(result);
    } finally {
      _departurePending = false;
    }
  }

  Future<void> _newSession(BuildContext context) async {
    if (_closing ||
        _departurePending ||
        controller.busy ||
        controller.session?.running == true) {
      return;
    }
    _departurePending = true;
    try {
      if (!await settingsLeaveGuard.requestLeave() ||
          !mounted ||
          !context.mounted) {
        return;
      }
      final approved = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                title: const Text('Start a new session?'),
                content: const Text(
                    'This closes the current session, clears its conversation and requests cancellation of its running jobs. Completed work is not undone. A new session uses the latest saved model configuration.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Start new session'))
                ],
              ));
      if (approved == true && mounted && !_closing) {
        await controller.newSession();
      }
    } finally {
      _departurePending = false;
    }
  }
}
