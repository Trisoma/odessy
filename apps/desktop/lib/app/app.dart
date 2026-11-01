import 'dart:async';
import 'dart:io';
import '../features/devices/controllers/device_monitoring_controller.dart';
import '../features/devices/presentation/devices.dart';
import '../features/devices/presentation/monitoring_details.dart';
import '../features/devices/presentation/monitoring_history.dart';
import '../features/shell/presentation/device_sidebar.dart';
import '../features/jobs/controllers/jobs_controller.dart';
import '../features/jobs/presentation/jobs_page.dart';
import 'connections/windows_credential_store.dart';
import '../features/monitoring/controllers/monitor_controller.dart';
import '../features/devices/presentation/devices_hub.dart';
import '../features/devices/presentation/execution_hosts_page.dart';
import 'execution_hosts_controller.dart';
import '../features/workspaces/presentation/workspace_page.dart';
import '../widgets/component_state_banner.dart';
import 'navigation.dart';
import 'readiness_controller.dart';
import '../features/conversation/controllers/voice_controller.dart';
import '../features/settings/controllers/component_settings_controller.dart';
import '../features/settings/presentation/settings_navigation.dart';
import '../features/settings/controllers/settings_form_controller.dart';
import '../features/settings/controllers/setting_field.dart';
import 'ux_copy.dart';
import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../features/conversation/presentation/conversation.dart';
import '../features/settings/presentation/settings.dart';
import 'session_controller.dart';
import 'theme.dart';
import 'connections/connection_dialog.dart';
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
import 'setup/installed_coordinator.dart';
import 'setup/session_model_credentials.dart';

class OdessyApp extends StatefulWidget {
  const OdessyApp(
      {super.key,
      this.api,
      this.installed,
      this.localDesktop = false,
      this.managementBackend,
      this.onExitApproved,
      this.onConfigured,
      this.onRestartSaved,
      this.onShutdownOwned,
      this.restoreConfiguration = false,
      this.setupBackend,
      this.coordinatorFactory});
  final InstalledCoordinatorHandle Function(Json)? coordinatorFactory;
  final bool restoreConfiguration;
  final SetupBackend? setupBackend;
  final VoidCallback? onConfigured;
  final Future<void> Function(String fingerprint)? onRestartSaved;
  final Future<void> Function()? onShutdownOwned;
  final OdessyApi? api;
  final bool localDesktop;
  final SetupBackend? managementBackend;
  final Future<void> Function()? onExitApproved;

  /// Recorded installed state when this process was started by the Windows
  /// launcher; null for the development preview.
  final Json? installed;
  @override
  State<OdessyApp> createState() => _OdessyAppState();
}

class _OdessyAppState extends State<OdessyApp> {
  late final DesktopSession controller;
  ExecutionHostsController? executionHosts;
  late final VoiceController voice;
  final monitoring = MonitorController();
  final deviceInventory = DeviceMonitoringController();
  late final JobsController jobs;
  late final SetupController setup;
  late final ComponentSettingsController componentSettings;
  final readiness = ReadinessController();
  OdessyApi? observedApi;
  bool restarting = false;
  ExecutionHostsController _executionHostController() {
    final api = controller.api;
    if (executionHosts == null ||
        !identical(
            executionHosts!.api, api is ExecutionHostsApi ? api : null)) {
      executionHosts?.dispose();
      executionHosts = ExecutionHostsController(
          api is ExecutionHostsApi ? api as ExecutionHostsApi : null,
          onConnected: controller.reloadExecutionTarget);
    }
    return executionHosts!;
  }

  void _observeConnection() {
    if (!identical(observedApi, controller.api)) {
      observedApi = controller.api;
      readiness.bind(controller.api);
      deviceInventory.bind(controller.api);
    }
  }

  Future<void> _restartSaved() async {
    if (restarting ||
        _settingsBusy ||
        controller.busy ||
        controller.session?.running == true) {
      return;
    }
    if (!await _mayNavigate(deferDiscard: true) || !mounted) return;
    restarting = true;
    final original = controller.api;
    String? ticket;
    try {
      if (original is! RuntimeConfigurationApi ||
          (configuredCoordinator == null && widget.onRestartSaved == null)) {
        throw StateError(
            'This service is not owned by this desktop. Restart it through its owner.');
      }
      await componentSettings.load();
      await readiness.bind(original);
      final saved = RuntimeConfiguration.fromJson(
          componentSettings.snapshot['configuration'] as Json);
      final observed = readiness.current;
      if (observed?.configuration?.owner != saved.owner) {
        throw StateError(
            'Running and saved configuration ownership could not be matched.');
      }
      final revision = componentSettings.snapshot['revision'];
      if (!mounted) return;
      final accepted = await showDialog<bool>(
          context: navigatorKey.currentContext!,
          builder: (context) => AlertDialog(
                  title: const Text('Restart with saved configuration?'),
                  content: Text(
                      'Startup model: ${observed!.configuration!.modelName}\nSaved model: ${saved.modelName}\nWorkspace: ${saved.workspace}\nThe idle conversation closes. Completed tools remain completed. No inference test or model download is run.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('Restart now'))
                  ]));
      if (accepted != true ||
          !mounted ||
          !identical(original, controller.api)) {
        return;
      }
      if (!await _discardApprovedSettings() || !mounted) return;
      await componentSettings.load();
      if (componentSettings.error.isNotEmpty ||
          componentSettings.snapshot['revision'] != revision) {
        throw StateError('Saved settings changed. Review the restart again.');
      }
      ticket = await (original as RuntimeConfigurationApi)
          .prepareRestart(observed!.serviceInstance);
      if (widget.installed != null) {
        await widget.onRestartSaved!(saved.fingerprint);
      } else {
        final reply = await setup.backend.execute(SetupRequest(
            id: 'restart-${DateTime.now().microsecondsSinceEpoch}',
            operation: 'resume',
            selection: const {}));
        if (!reply.ok) throw StateError(reply.reason);
        await _connectConfigured(
            {...reply.result, 'expected_fingerprint': saved.fingerprint});
      }
      ticket = null;
    } catch (e) {
      if (mounted) {
        setState(() => connectionNotice = e is StateError
            ? e.message.toString()
            : 'Restart failed. Inspect the saved configuration and local service before retrying.');
      }
    } finally {
      if (ticket != null && original is RuntimeConfigurationApi) {
        try {
          await (original as RuntimeConfigurationApi).resumeAdmission(ticket);
        } catch (_) {/* Owned process may already be stopped. */}
      }
      restarting = false;
    }
  }

  final navigatorKey = GlobalKey<NavigatorState>();
  final scaffoldKey = GlobalKey<ScaffoldState>();
  final navigationFocus = <String, FocusNode>{};
  bool _navigating = false;

  final collaborationForms = <OdessyApi, SettingsFormController<int>>{};
  SettingsFormController<int> _collaborationForm() =>
      collaborationForms.putIfAbsent(controller.api, () {
        final owner = controller.api;
        return SettingsFormController<int>(
            field: SettingField<int>(
                source: 'service collaboration settings',
                applyTiming: 'new sessions',
                parse: int.tryParse,
                validate: (v) => v < 0 || v > 32
                    ? 'Enter a whole number from 0 to 32.'
                    : null),
            read: () async {
              final result = await controller.loadSettings();
              if (!identical(owner, controller.api)) {
                throw StateError('Connection changed');
              }
              return (result['settings'] as Map)['max_consultations'] as int;
            },
            write: (value) async {
              if (!identical(owner, controller.api)) {
                throw StateError('Connection changed');
              }
              await controller.updateConsultationLimit(value);
            });
      });
  bool get _settingsBusy =>
      componentSettings.busy || collaborationForms.values.any((f) => f.loading);
  InstalledCoordinatorHandle? configuredCoordinator;
  int configuredEpoch = 0;
  late final MaintenanceController maintenance, pairing, installerEngine;
  InstalledManageController? installManagement;
  final CredentialStore credentials = MemoryCredentialStore();
  final CredentialStore? credentialVault =
      Platform.isWindows ? WindowsCredentialStore() : null;
  late final AppLifecycleListener lifecycle;
  ThemeMode mode = ThemeMode.dark;
  String page = 'Conversation';
  int setupStep = 0;
  String settingsSection = SettingsSections.general;
  String connectionNotice = '';
  Future<void> _activateSaved() async {
    if (restarting) return;
    if (configuredCoordinator != null || controller.connected) {
      await _restartSaved();
      return;
    }
    if (setup.busy ||
        _settingsBusy ||
        setup.completing ||
        controller.busy ||
        controller.session?.running == true) {
      return;
    }
    setState(() => connectionNotice = '');
    if (widget.installed != null) {
      widget.onConfigured?.call();
      return;
    }
    await setup.restore();
    if (!mounted) return;
    setState(() => connectionNotice = setup.error.isNotEmpty
        ? setup.error
        : controller.connected
            ? ''
            : 'No saved model is available. Choose a model in Settings first.');
  }

  Future<void> _openSettings(
      [String section = SettingsSections.general]) async {
    if (page == 'Settings' && settingsSection == section) {
      scaffoldKey.currentState?.closeDrawer();
      return;
    }
    if (!await _mayNavigate() || !mounted) return;
    setState(() {
      page = 'Settings';
      settingsSection = section;
    });
    scaffoldKey.currentState?.closeDrawer();
  }

  Future<void> _navigate(String route) async {
    if (route == page) {
      scaffoldKey.currentState?.closeDrawer();
      return;
    }
    if (!await _mayNavigate() || !mounted) return;
    setState(() => page = route);
    scaffoldKey.currentState?.closeDrawer();
  }

  Future<bool> _discardApprovedSettings() async {
    final form = collaborationForms[controller.api];
    if (form != null && form.dirty) {
      await form.load();
      if (form.error != null || form.dirty) return false;
    }
    final section = componentSettings.activeSection;
    if (section != null && componentSettings.dirty) {
      componentSettings.discard(section);
    }
    return true;
  }

  Future<bool> _mayNavigate({bool deferDiscard = false}) async {
    if (_navigating || _settingsBusy) return false;
    final context = navigatorKey.currentContext;
    if (context == null) return true;
    _navigating = true;
    try {
      if (!await confirmSettingsNavigation(context, componentSettings,
          deferDiscard: true)) {
        return false;
      }
      if (!mounted || !context.mounted) return false;
      if (!await confirmFormNavigation(
          context, collaborationForms[controller.api], deferDiscard: true)) {
        return false;
      }
      return deferDiscard || await _discardApprovedSettings();
    } finally {
      _navigating = false;
    }
  }

  @override
  void initState() {
    super.initState();
    controller = DesktopSession(widget.api ?? DisconnectedApi());
    jobs = JobsController(controller);
    voice = VoiceController(controller);
    controller.addListener(_observeConnection);
    _observeConnection();
    setup = SetupController(widget.setupBackend ?? LocalSetupBackend(),
        onConnect: _connectConfigured, onCancelConnect: () async {
      ++configuredEpoch;
      await configuredCoordinator?.stop();
    })
      ..output = LocalSetupBackend.defaultOutput();
    componentSettings = ComponentSettingsController(setup.backend,
        root: widget.installed?['root'] as String? ?? '');
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
    lifecycle = AppLifecycleListener(onExitRequested: () async {
      if (!await _mayNavigate()) return AppExitResponse.cancel;
      await componentSettings.shutdown();
      for (final form in collaborationForms.values) {
        await form.shutdown();
      }
      await controller.shutdown();
      await setup.shutdown();
      await configuredCoordinator?.stop();
      await widget.onShutdownOwned?.call();
      await widget.onExitApproved?.call();
      await deviceInventory.shutdown();
      await maintenance.shutdown();
      await installerEngine.shutdown();
      await pairing.shutdown();
      installManagement?.dispose();
      installManagement = null;
      return AppExitResponse.exit;
    });
    if (widget.restoreConfiguration && widget.installed == null) {
      _restoreConfiguration();
    } else {
      controller.start();
    }
  }

  Future<void> _restoreConfiguration() async {
    await controller.start();
    if (!mounted) return;
    await setup.restore();
    if (mounted && setup.error.isNotEmpty) setState(() => page = 'Settings');
  }

  @override
  void dispose() {
    unawaited(sessionModelCredentials.clear().catchError((Object _) {}));
    for (final node in navigationFocus.values) {
      node.dispose();
    }
    lifecycle.dispose();
    monitoring.dispose();
    deviceInventory.dispose();
    jobs.dispose();
    controller.removeListener(_observeConnection);
    executionHosts?.dispose();
    voice.dispose();
    readiness.dispose();
    componentSettings.dispose();
    for (final form in collaborationForms.values) {
      form.dispose();
    }
    controller.dispose();
    setup.dispose();
    configuredCoordinator?.stop();
    maintenance.dispose();
    installerEngine.dispose();
    pairing.dispose();
    installManagement?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
      navigatorKey: navigatorKey,
      title: 'Odessy',
      debugShowCheckedModeBanner: false,
      theme: odessyTheme(Brightness.light),
      darkTheme: odessyTheme(Brightness.dark),
      themeMode: mode,
      home: ListenableBuilder(
          listenable: Listenable.merge([
            controller,
            setup,
            componentSettings,
            readiness,
            voice,
            deviceInventory
          ]),
          builder: (context, _) =>
              LayoutBuilder(builder: (context, constraints) {
                final narrow = constraints.maxWidth < 900;
                final sidebar = _sidebar(context);
                return Scaffold(
                    key: scaffoldKey,
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
                              if (DesktopNavigation.parent(page) != page)
                                IconButton(
                                    tooltip:
                                        'Back to ${DesktopNavigation.title(DesktopNavigation.parent(page))}',
                                    onPressed: () => _navigate(
                                        DesktopNavigation.parent(page)),
                                    icon:
                                        const Icon(Icons.arrow_back, size: 20)),
                              Text(DesktopNavigation.title(page),
                                  style:
                                      Theme.of(context).textTheme.titleMedium),
                              const Spacer(),
                              _badge(
                                  context,
                                  controller.api is DisconnectedApi
                                      ? (setup.saved ||
                                              setup.activeSelection != null
                                          ? 'OFFLINE'
                                          : 'NOT CONFIGURED')
                                      : controller.api.isDemo
                                          ? 'DEMO'
                                          : controller.connected
                                              ? 'CONNECTED'
                                              : 'DISCONNECTED',
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
                                    onPressed: controller.busy ||
                                            controller.session?.running == true
                                        ? null
                                        : () => _newSession(context)),
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
                        if ([
                          'preparing',
                          'recording',
                          'transcribing',
                          'speaking',
                          'cancelling'
                        ].contains(voice.snapshot?.state))
                          ComponentStateBanner(
                              message:
                                  'Audio ${voice.snapshot!.state} on ${voice.snapshot!.host}. Cancel stops audio; completed actions remain completed.',
                              actions: [
                                TextButton(
                                    onPressed: () => voice.command(
                                        voice.snapshot?.captureId == null
                                            ? 'stop_playback'
                                            : 'cancel_capture'),
                                    child: const Text('Cancel audio'))
                              ]),
                        if (controller.api is DisconnectedApi)
                          ComponentStateBanner(
                              message: connectionNotice.isNotEmpty
                                  ? connectionNotice
                                  : setup.saved || setup.activeSelection != null
                                      ? DesktopCopy.offline
                                      : DesktopCopy.unconfigured,
                              actions: [
                                TextButton(
                                    onPressed: () =>
                                        _openSettings(SettingsSections.models),
                                    child: const Text('Configure model')),
                                TextButton(
                                    onPressed: setup.busy ||
                                            setup.completing ||
                                            controller.session?.running ==
                                                true ||
                                            (widget.installed != null &&
                                                widget.onConfigured == null)
                                        ? null
                                        : _activateSaved,
                                    child: const Text(
                                        'Connect saved configuration')),
                              ]),
                        if (controller.error.isNotEmpty)
                          ComponentStateBanner(
                              message: controller.error,
                              tone: BannerTone.error,
                              actions: [
                                TextButton(
                                    onPressed: controller.refresh,
                                    child: const Text('Refresh'))
                              ]),
                        if (connectionNotice.isNotEmpty &&
                            controller.api is! DisconnectedApi)
                          ComponentStateBanner(
                              message: connectionNotice,
                              tone: BannerTone.error),
                        Expanded(
                            child: switch (page) {
                          'Device details' => _devicePage(),
                          'Jobs' => JobsPage(controller: jobs),
                          'Monitor' || 'Devices' => DevicesHub(
                              session: controller,
                              monitoring: monitoring,
                              initialPerformance: page == 'Monitor',
                              onConnect: () => _navigate('Pairing')),
                          'Workspaces' => WorkspacePage(
                              readiness: readiness,
                              connected: controller.connected,
                              componentController: componentSettings,
                              backend: setup.backend,
                              root: widget.installed?['root'] as String? ?? '',
                              picker: const FileSelectorPicker()),
                          'Setup' => SetupWizardPage(
                              key: ValueKey(setupStep),
                              controller: setup,
                              initialStep: setupStep,
                              picker: const FileSelectorPicker()),
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
                          'Pairing' => ExecutionHostsPage(
                              key: const ValueKey('execution-hosts'),
                              controller: _executionHostController()),
                          'Local pairing' => MaintenancePage(
                              key: const ValueKey('pairing'),
                              controller: pairing,
                              pairing: true),
                          'Settings' => SettingsPage(
                              readiness: readiness,
                              onRestartSaved: ((configuredCoordinator != null ||
                                          widget.onRestartSaved != null) &&
                                      readiness.current?.configuration != null)
                                  ? _restartSaved
                                  : null,
                              key: ValueKey((controller.api, settingsSection)),
                              initialSection: settingsSection,
                              controller: controller,
                              componentController: componentSettings,
                              configurationBackend: setup.backend,
                              formController: _collaborationForm(),
                              installationRoot:
                                  widget.installed?['root'] as String? ?? '',
                              installed: widget.installed != null,
                              modelConfigured:
                                  (widget.installed?['model_configured'] == true ||
                                      (setup.activeSelection ??
                                              (setup.saved
                                                  ? setup.selection
                                                  : const {}))['model'] !=
                                          null),
                              voiceEnabled:
                                  (widget.installed?['voice_enabled'] == true ||
                                      ((setup.activeSelection ?? (setup.saved ? setup.selection : const {}))['voice']
                                              as Map?)?['enabled'] ==
                                          true),
                              themeMode: mode,
                              onToggleTheme: () => setState(() => mode = mode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark),
                              onOpenDevices: () => _navigate('Devices'),
                              onConnectSaved: setup.busy || setup.completing || controller.session?.running == true || (widget.installed != null && widget.onConfigured == null) ? null : _activateSaved,
                              onOpenInstaller: () => _navigate('Installer'),
                              onOpenMaintenance: () => _navigate('Maintenance'),
                              maintenanceBlocked: controller.session?.running == true,
                              onOpenGetStarted: () => setState(() {
                                    page = 'Settings';
                                  }),
                              onOpenVoice: () => setState(() {
                                    page = 'Settings';
                                  }),
                              onOpenManagement: widget.installed == null ? null : () => setState(() => page = 'Manage installation')),
                          _ => ConversationPage(
                              voice: voice,
                              controller: controller,
                              readiness: readiness),
                        }),
                      ])),
                    ]));
              })));
  Future<void> _inspectDevice(String id) async {
    if (!await _mayNavigate() || !mounted) return;
    deviceInventory.select(id);
    setState(() => page = 'Device details');
    scaffoldKey.currentState?.closeDrawer();
  }

  Widget _sidebar(BuildContext context) => DeviceSidebar(
          hosts: deviceInventory.hosts,
          conversationLabel: 'Assistant',
          navigationFocus: navigationFocus,
          selectedHostId:
              page == 'Device details' ? deviceInventory.selectedId : null,
          conversationSelected: page == 'Conversation',
          settingsSelected: page == 'Settings',
          loading: deviceInventory.loadingInventory,
          retained: deviceInventory.inventoryRetained,
          error: deviceInventory.inventoryError,
          demo: deviceInventory.demo,
          connectionLabel: controller.api.isDemo
              ? 'Demo · no tools execute'
              : controller.api.identity,
          onConversation: () => _navigate('Conversation'),
          onSettings: _openSettings,
          onInspect: _inspectDevice,
          onRefresh: () => unawaited(deviceInventory.refresh()),
          onPair: () => _navigate('Pairing'),
          primaryDestinations: [
            SidebarDestination(
                label: 'Workspaces',
                icon: Icons.folder_open_outlined,
                selected: page == 'Workspaces',
                onTap: () => _navigate('Workspaces')),
            SidebarDestination(
                label: 'Devices',
                icon: Icons.devices_outlined,
                selected: page == 'Devices' || page == 'Monitor',
                onTap: () => _navigate('Devices')),
          ],
          destinations: [
            SidebarDestination(
                label: 'Jobs',
                icon: Icons.work_outline,
                selected: page == 'Jobs',
                onTap: () => _navigate('Jobs')),
            SidebarDestination(
                label: 'Installation',
                icon: Icons.settings_outlined,
                selected: page == 'Installer' || page == 'Maintenance',
                onTap: () => _openSettings(SettingsSections.installation)),
            SidebarDestination(
                label: 'Runtime maintenance',
                icon: Icons.handyman_outlined,
                selected: page == 'Maintenance',
                onTap: controller.session?.running == true
                    ? null
                    : () => _navigate('Maintenance')),
            SidebarDestination(
                label: 'Local pairing tools',
                icon: Icons.shield_outlined,
                selected: page == 'Local pairing',
                onTap: () => _navigate('Local pairing')),
          ]);

  Widget _devicePage() => DevicePage(
      key: ValueKey(deviceInventory.selectedId),
      device: deviceInventory.selectedHost?.name ?? '',
      controller: controller,
      onManagePairing: () => _navigate('Local pairing'),
      monitoringOverview: MonitoringDetails(
          showHeader: false,
          host: deviceInventory.inspectedHost,
          report: deviceInventory.metrics,
          loading: deviceInventory.loadingMetrics ||
              deviceInventory.loadingInventory,
          retained: deviceInventory.metricsRetained,
          error: deviceInventory.metricsError ?? deviceInventory.inventoryError,
          demo: deviceInventory.demo,
          onRefresh: () => unawaited(deviceInventory.refreshSelected())),
      monitoringHistory:
          Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DropdownButton<HistoryWindow>(
            key: const Key('monitoring-history-window'),
            isExpanded: true,
            value: deviceInventory.window,
            items: [
              for (final item in HistoryWindow.values)
                DropdownMenuItem(value: item, child: Text(item.label))
            ],
            onChanged: (value) {
              if (value != null) deviceInventory.setWindow(value);
            }),
        MonitoringHistory(
            host: deviceInventory.inspectedHost,
            report: deviceInventory.history,
            loading: deviceInventory.loadingHistory,
            retained: deviceInventory.historyRetained,
            error: deviceInventory.historyError,
            degradation: deviceInventory.degradation,
            demo: deviceInventory.demo,
            onRefresh: () => unawaited(deviceInventory.refreshHistory())),
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

  Future<void> _newSession(BuildContext context) async {
    if (!await _mayNavigate(deferDiscard: true) || !context.mounted) return;
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
                    child: const Text('Start new session')),
              ],
            ));
    if (approved == true && mounted && await _discardApprovedSettings()) {
      await controller.newSession();
    }
  }

  Future<void> _connectConfigured(Json spec) async {
    if (_settingsBusy) {
      throw StateError(
          'Wait for the current settings operation before changing connection.');
    }
    final epoch = ++configuredEpoch;
    if (controller.busy || controller.session?.running == true) {
      throw StateError(
          'Stop the active conversation before changing configuration.');
    }
    final previous = configuredCoordinator;
    await previous?.stop();
    if (identical(configuredCoordinator, previous)) {
      configuredCoordinator = null;
    }
    await controller.replace(DisconnectedApi());
    if (!mounted || epoch != configuredEpoch) return;
    final child = widget.coordinatorFactory?.call(spec) ??
        InstalledCoordinator(
          python: spec['python'] as String,
          root: '',
          workspace: spec['workspace'] as String,
          configuration: spec,
          expectedFingerprint: spec['expected_fingerprint'] as String?,
        );
    configuredCoordinator = child;
    try {
      final api = await child.start();
      if (!mounted || epoch != configuredEpoch) {
        api.close();
        await child.stop();
        if (identical(configuredCoordinator, child)) {
          configuredCoordinator = null;
        }
        return;
      }
      await controller.replace(api);
      if (!controller.connected) throw StateError(controller.error);
      monitoring.bind(controller.api);
      if (mounted) setState(() => page = 'Conversation');
    } catch (_) {
      await child.stop();
      if (identical(configuredCoordinator, child)) configuredCoordinator = null;
      rethrow;
    }
  }

  Future<void> _connect(BuildContext context) async {
    if (restarting) return;
    if (_settingsBusy) {
      await showDialog<void>(
          context: context,
          builder: (context) => AlertDialog(
                  title: const Text('Settings operation in progress'),
                  content: const Text(
                      'Wait for the current save or inspection before changing the connection.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('Close'))
                  ]));
      return;
    }
    if (!await _mayNavigate(deferDiscard: true) || !context.mounted) return;
    final result = await showConnectionDialog(context, credentials,
        vault: credentialVault,
        allowDemo: controller.api.isDemo && !widget.localDesktop);
    if (!mounted) {
      result?.close();
      return;
    }
    if (result != null) {
      if (!await _discardApprovedSettings() || !mounted) {
        result.close();
        return;
      }
      await controller.replace(result);
      monitoring.bind(controller.api);
    }
  }
}
