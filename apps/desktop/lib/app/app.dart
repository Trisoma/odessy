import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../features/conversation/presentation/conversation.dart';
import '../features/devices/presentation/devices.dart';
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

class OdessyApp extends StatefulWidget {
  const OdessyApp({super.key, this.api, this.installed, this.onConfigured});
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
  late final SetupController setup;
  late final MaintenanceController maintenance, pairing, installerEngine;
  InstalledManageController? installManagement;
  final CredentialStore credentials = MemoryCredentialStore();
  late final AppLifecycleListener lifecycle;
  ThemeMode mode = ThemeMode.dark;
  String page = 'Conversation', device = 'This computer';
  @override
  void initState() {
    super.initState();
    controller = DesktopSession(widget.api ?? DemoApi());
    setup = SetupController(LocalSetupBackend());
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
      await maintenance.shutdown();
      await installerEngine.shutdown();
      await pairing.shutdown();
      installManagement?.dispose();
      return AppExitResponse.exit;
    });
    controller.start();
  }

  @override
  void dispose() {
    lifecycle.dispose();
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
      title: 'Odessy · Desktop preview',
      debugShowCheckedModeBanner: false,
      theme: odessyTheme(Brightness.light),
      darkTheme: odessyTheme(Brightness.dark),
      themeMode: mode,
      home: ListenableBuilder(
          listenable: controller,
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
                          MaterialBanner(
                              content: const Text(
                                  'Model not configured — configure a model to enable chat. No model is running.'),
                              actions: [
                                TextButton(
                                    onPressed: () =>
                                        setState(() => page = 'Get started'),
                                    child: const Text('Configure model')),
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
                          'Devices' =>
                            DevicePage(device: device, controller: controller),
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
                              onOpenDevices: () => setState(() {
                                    device = 'This computer';
                                    page = 'Devices';
                                  }),
                              onOpenGetStarted: installManagement == null
                                  ? null
                                  : () => setState(() => page = 'Get started'),
                              onOpenVoice: installManagement == null
                                  ? null
                                  : () => setState(() => page = 'Voice setup'),
                              onOpenManagement: widget.installed == null
                                  ? null
                                  : () => setState(
                                      () => page = 'Manage installation')),
                          _ => ConversationPage(controller: controller),
                        }),
                      ])),
                    ]));
              })));
  Widget _sidebar(BuildContext context) => SafeArea(
      child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 22, 16, 16),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(Icons.bubble_chart_outlined,
                  color: Theme.of(context).colorScheme.primary, size: 30),
              const SizedBox(width: 10),
              const Text('Odessy',
                  style: TextStyle(
                      fontSize: 23,
                      fontWeight: FontWeight.w600,
                      letterSpacing: -.7))
            ]),
            const Padding(
                padding: EdgeInsets.only(left: 40, top: 2, bottom: 34),
                child: Text('DESKTOP PREVIEW · 0.5',
                    style: TextStyle(fontSize: 9, letterSpacing: 1.3))),
            _nav(
                context,
                'Conversation',
                Icons.chat_bubble_outline,
                page == 'Conversation',
                () => setState(() => page = 'Conversation')),
            if (widget.installed != null) ...[
              const SizedBox(height: 6),
              _nav(
                  context,
                  'Manage installation',
                  Icons.settings_outlined,
                  page == 'Manage installation',
                  () => setState(() => page = 'Manage installation')),
              if (widget.installed?['model_configured'] != true)
                _nav(
                    context,
                    'Get started',
                    Icons.rocket_launch_outlined,
                    page == 'Get started',
                    () => setState(() => page = 'Get started')),
            ],
            const SizedBox(height: 28),
            const Text('  DEVICES',
                style: TextStyle(
                    fontSize: 10, letterSpacing: 1.6, color: Colors.grey)),
            const SizedBox(height: 10),
            _nav(
                context,
                'This computer',
                Icons.desktop_windows_outlined,
                page == 'Devices' && device == 'This computer',
                () => setState(() {
                      device = 'This computer';
                      page = 'Devices';
                    })),
            if (controller.api.isDemo) ...[
              _nav(
                  context,
                  'Jetson',
                  Icons.memory_outlined,
                  page == 'Devices' && device == 'Jetson',
                  () => setState(() {
                        device = 'Jetson';
                        page = 'Devices';
                      })),
              _nav(
                  context,
                  'Laptop host',
                  Icons.laptop_outlined,
                  page == 'Devices' && device == 'Laptop host',
                  () => setState(() {
                        device = 'Laptop host';
                        page = 'Devices';
                      })),
            ],
            const SizedBox(height: 12),
            TextButton.icon(
                onPressed: () => setState(() {
                      page = 'Pairing';
                    }),
                icon: const Icon(Icons.add, size: 16),
                label: const Text('Pair a device')),
            const Spacer(),
            _nav(context, 'Set up this computer', Icons.build_outlined,
                page == 'Setup', () {
              if (controller.session?.running != true) {
                setState(() => page = 'Setup');
              }
            }),
            _nav(context, 'Install Odessy', Icons.download_outlined,
                page == 'Installer', () {
              if (controller.session?.running != true) {
                setState(() => page = 'Installer');
              }
            }),
            _nav(context, 'Runtime maintenance', Icons.handyman_outlined,
                page == 'Maintenance', () {
              if (controller.session?.running != true) {
                setState(() => page = 'Maintenance');
              }
            }),
            const Divider(),
            const SizedBox(height: 10),
            _nav(context, 'Settings', Icons.tune, page == 'Settings',
                () => setState(() => page = 'Settings')),
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
      Padding(
          padding: const EdgeInsets.only(bottom: 5),
          child: ListTile(
              dense: true,
              selected: selected,
              selectedTileColor:
                  Theme.of(context).colorScheme.primary.withValues(alpha: .12),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(10)),
              contentPadding: const EdgeInsets.symmetric(horizontal: 12),
              leading: Icon(icon, size: 19),
              title: Text(label, style: const TextStyle(fontSize: 13)),
              onTap: tap));
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
  Future<void> _connect(BuildContext context) async {
    final result = await showConnectionDialog(context, credentials);
    if (!mounted) {
      result?.close();
      return;
    }
    if (result != null) await controller.replace(result);
  }
}
