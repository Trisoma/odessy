import '../features/monitoring/controllers/monitor_controller.dart';
import '../features/devices/presentation/devices_hub.dart';
import '../features/workspaces/presentation/workspace_page.dart';
import '../widgets/component_state_banner.dart';
import '../widgets/pixel_boat.dart';
import 'navigation.dart';
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

class OdessyApp extends StatefulWidget {
  const OdessyApp(
      {super.key,
      this.api,
      this.installed,
      this.onConfigured,
      this.restoreConfiguration = false,
      this.setupBackend,
      this.coordinatorFactory});
  final InstalledCoordinatorHandle Function(Json)? coordinatorFactory;
  final bool restoreConfiguration;
  final SetupBackend? setupBackend;
  final VoidCallback? onConfigured;
  final OdessyApi? api;

  /// Recorded installed state when this process was started by the Windows
  /// launcher; null for the development preview.
  final Json? installed;
  @override
  State<OdessyApp> createState() => _OdessyAppState();
}

class _OdessyAppState extends State<OdessyApp> {
  late final DesktopSession controller;
  final monitoring = MonitorController();
  late final SetupController setup;
  InstalledCoordinatorHandle? configuredCoordinator;
  int configuredEpoch = 0;
  late final MaintenanceController maintenance, pairing, installerEngine;
  InstalledManageController? installManagement;
  final CredentialStore credentials = MemoryCredentialStore();
  late final AppLifecycleListener lifecycle;
  ThemeMode mode = ThemeMode.dark;
  String page = 'Conversation';
  int setupStep = 0;
  String settingsSection = SettingsSections.general;
  String connectionNotice = '';
  Future<void> _activateSaved() async {
    if (setup.busy ||
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

  void _openSettings([String section = SettingsSections.general]) {
    setState(() {
      page = 'Settings';
      settingsSection = section;
    });
  }

  void _navigate(String route) {
    setState(() => page = route);
  }

  @override
  void initState() {
    super.initState();
    controller = DesktopSession(widget.api ?? DemoApi());
    setup = SetupController(widget.setupBackend ?? LocalSetupBackend(),
        onConnect: _connectConfigured, onCancelConnect: () async {
      ++configuredEpoch;
      await configuredCoordinator?.stop();
    })
      ..output = LocalSetupBackend.defaultOutput();
    maintenance = MaintenanceController(LocalSetupBackend());
    installerEngine = MaintenanceController(LocalSetupBackend());
    pairing = MaintenanceController(LocalSetupBackend());
    final installed = widget.installed;
    if (installed != null) {
      installManagement = InstalledManageController(LocalSetupBackend(),
          spec: {...installed, 'root': installed['root']})
        ..load();
    }
    lifecycle = AppLifecycleListener(onExitRequested: () async {
      await controller.shutdown();
      await setup.shutdown();
      await configuredCoordinator?.stop();
      await maintenance.shutdown();
      await installerEngine.shutdown();
      await pairing.shutdown();
      installManagement?.dispose();
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
    lifecycle.dispose();
    monitoring.dispose();
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
      title: 'Odessy · Desktop preview',
      debugShowCheckedModeBanner: false,
      theme: odessyTheme(Brightness.light),
      darkTheme: odessyTheme(Brightness.dark),
      themeMode: mode,
      home: ListenableBuilder(
          listenable: Listenable.merge([controller, setup]),
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
                        Expanded(
                            child: switch (page) {
                          'Monitor' || 'Devices' => DevicesHub(
                              session: controller,
                              monitoring: monitoring,
                              initialPerformance: page == 'Monitor',
                              onConnect: () => _navigate('Pairing')),
                          'Workspaces' => WorkspacePage(
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
                          'Pairing' => MaintenancePage(
                              key: const ValueKey('pairing'),
                              controller: pairing,
                              pairing: true),
                          'Settings' => SettingsPage(
                              key: ValueKey((controller.api, settingsSection)),
                              initialSection: settingsSection,
                              controller: controller,
                              configurationBackend: setup.backend,
                              installationRoot:
                                  widget.installed?['root'] as String? ?? '',
                              installed: widget.installed != null,
                              modelConfigured: (widget.installed?['model_configured'] == true ||
                                  (setup.activeSelection ?? (setup.saved ? setup.selection : const {}))['model'] !=
                                      null),
                              voiceEnabled: (widget.installed?['voice_enabled'] == true ||
                                  ((setup.activeSelection ?? (setup.saved ? setup.selection : const {}))['voice'] as Map?)?['enabled'] ==
                                      true),
                              themeMode: mode,
                              onToggleTheme: () => setState(() => mode = mode == ThemeMode.dark
                                  ? ThemeMode.light
                                  : ThemeMode.dark),
                              onOpenDevices: () => _navigate('Devices'),
                              onConnectSaved: setup.busy ||
                                      setup.completing ||
                                      controller.session?.running == true ||
                                      (widget.installed != null && widget.onConfigured == null)
                                  ? null
                                  : _activateSaved,
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
                          _ => ConversationPage(controller: controller),
                        }),
                      ])),
                    ]));
              })));
  Widget _sidebar(BuildContext context) => SafeArea(
      child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 22, 16, 16),
          child: ListView(children: [
            Row(children: [
              const PixelBoat(size: 30),
              const SizedBox(width: 10),
              const Expanded(
                  child: Text('Odessy',
                      style: TextStyle(
                          fontSize: 23,
                          fontWeight: FontWeight.w600,
                          letterSpacing: -.7)))
            ]),
            const Padding(
                padding: EdgeInsets.only(left: 40, top: 2, bottom: 34),
                child: Text('DESKTOP PREVIEW · 0.5',
                    style: TextStyle(fontSize: 9, letterSpacing: 1.3))),
            for (final destination in DesktopNavigation.primary)
              _nav(
                  context,
                  destination.label,
                  destination.icon,
                  DesktopNavigation.parent(page) == destination.route,
                  () => _navigate(destination.route)),
            const SizedBox(height: 24),
            const Divider(),
            _nav(
                context,
                DesktopNavigation.settings.label,
                DesktopNavigation.settings.icon,
                DesktopNavigation.parent(page) == 'Settings',
                () => _openSettings()),
            const SizedBox(height: 18),
            Row(children: [
              Icon(Icons.circle,
                  size: 7,
                  color: controller.connected
                      ? const Color(0xff9bb9a7)
                      : Colors.orange),
              const SizedBox(width: 8),
              Expanded(
                  child: Text(
                      controller.api.isDemo
                          ? 'Demo · no tools execute'
                          : controller.api.identity,
                      style: const TextStyle(fontSize: 11),
                      overflow: TextOverflow.ellipsis))
            ]),
          ])));
  Widget _nav(BuildContext context, String label, IconData icon, bool selected,
          VoidCallback tap) =>
      Builder(
          builder: (tileContext) => Padding(
              padding: const EdgeInsets.only(bottom: 5),
              child: ListTile(
                  key: ValueKey('nav-$label'),
                  dense: true,
                  selected: selected,
                  selectedTileColor: Theme.of(context)
                      .colorScheme
                      .primary
                      .withValues(alpha: .12),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10)),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 12),
                  leading: Icon(icon, size: 19),
                  title: Text(label, style: const TextStyle(fontSize: 13)),
                  onTap: () {
                    Scaffold.maybeOf(tileContext)?.closeDrawer();
                    tap();
                  })));
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
  Future<void> _connectConfigured(Json spec) async {
    final epoch = ++configuredEpoch;
    if (controller.busy || controller.session?.running == true) {
      throw StateError(
          'Stop the active conversation before changing configuration.');
    }
    await configuredCoordinator?.stop();
    await controller.replace(DisconnectedApi());
    if (!mounted || epoch != configuredEpoch) return;
    final child = widget.coordinatorFactory?.call(spec) ??
        InstalledCoordinator(
          python: spec['python'] as String,
          root: '',
          workspace: spec['workspace'] as String,
          configuration: spec,
        );
    configuredCoordinator = child;
    try {
      final api = await child.start();
      if (!mounted || epoch != configuredEpoch) {
        api.close();
        await child.stop();
        return;
      }
      await controller.replace(api);
      if (!controller.connected) throw StateError(controller.error);
      monitoring.bind(controller.api);
      if (mounted) setState(() => page = 'Conversation');
    } catch (_) {
      await child.stop();
      rethrow;
    }
  }

  Future<void> _connect(BuildContext context) async {
    final result = await showConnectionDialog(context, credentials);
    if (!mounted) {
      result?.close();
      return;
    }
    if (result != null) {
      await controller.replace(result);
      monitoring.bind(controller.api);
    }
  }
}
