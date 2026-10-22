import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../features/conversation/presentation/conversation.dart';
import '../features/devices/presentation/devices.dart';
import '../features/settings/presentation/settings.dart';
import 'session_controller.dart';
import 'theme.dart';

class OdessyApp extends StatefulWidget {
  const OdessyApp({super.key, this.api});
  final OdessyApi? api;
  @override
  State<OdessyApp> createState() => _OdessyAppState();
}

class _OdessyAppState extends State<OdessyApp> {
  late final DesktopSession controller;
  ThemeMode mode = ThemeMode.dark;
  String page = 'Conversation', device = 'This computer';
  @override
  void initState() {
    super.initState();
    controller = DesktopSession(widget.api ?? DemoApi());
    controller.start();
  }

  @override
  void dispose() {
    controller.dispose();
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
                      if (!narrow) SizedBox(width: 236, child: sidebar),
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
                                  controller.api.isDemo ? 'DEMO' : 'LOCAL API',
                                  controller.api.isDemo
                                      ? Colors.amber
                                      : Colors.green),
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
                          'Devices' =>
                            DevicePage(device: device, controller: controller),
                          'Settings' => SettingsPage(
                              key: ValueKey(controller.api),
                              controller: controller),
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
                      page = 'Devices';
                      device = 'This computer';
                    }),
                icon: const Icon(Icons.add, size: 16),
                label: const Text('Pair a device')),
            const Spacer(),
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
    final endpoint = TextEditingController(text: 'http://127.0.0.1:8765');
    final token = TextEditingController();
    String error = '';
    final result = await showDialog<OdessyApi>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                    title: const Text('Connect to Odessy'),
                    content: SizedBox(
                        width: 420,
                        child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                  'Use the address and token from odessy serve. The token stays in memory. Remote engines require a preconfigured local tunnel.'),
                              const SizedBox(height: 20),
                              TextField(
                                  controller: endpoint,
                                  decoration: const InputDecoration(
                                      labelText: 'Local API address')),
                              const SizedBox(height: 12),
                              TextField(
                                  controller: token,
                                  obscureText: true,
                                  enableSuggestions: false,
                                  autocorrect: false,
                                  decoration: const InputDecoration(
                                      labelText: 'Bearer token')),
                              if (error.isNotEmpty)
                                Padding(
                                    padding: const EdgeInsets.only(top: 12),
                                    child: Text(error,
                                        style: TextStyle(
                                            color: Theme.of(context)
                                                .colorScheme
                                                .error))),
                            ])),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(context, DemoApi()),
                          child: const Text('Use demo')),
                      TextButton(
                          onPressed: () => Navigator.pop(context),
                          child: const Text('Cancel')),
                      FilledButton(
                          onPressed: () {
                            try {
                              Navigator.pop(
                                  context,
                                  LocalApi(Uri.parse(endpoint.text.trim()),
                                      token.text));
                            } catch (_) {
                              update(() => error =
                                  'Enter a valid loopback address and a bearer token.');
                            }
                          },
                          child: const Text('Connect'))
                    ])));
    // Dialog route transitions may still reference controllers until the next frame.
    await Future<void>.delayed(const Duration(milliseconds: 300));
    endpoint.dispose();
    token.dispose();
    if (result != null) await controller.replace(result);
  }
}
