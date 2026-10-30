import 'dart:async';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';
import '../../../app/theme.dart';
import '../controllers/setting_field.dart';
import '../controllers/settings_form_controller.dart';
import 'runtime_inventory.dart';
import 'model_profiles.dart';
import 'collaboration_setting_dialog.dart';

class SettingsSections {
  static const general = 'General';
  static const models = 'Models and connections';
  static const voice = 'Voice';
  static const devices = 'Devices and tool access';
  static const advanced = 'Advanced';
  static const installation = 'Installation';
  static const all = [general, models, voice, devices, advanced, installation];
}

/// Optional shell binding. The mounted page owns the form and its confirmation.
class SettingsLeaveGuard {
  Object? _owner;
  Future<bool> Function()? _confirm;
  void bind(Object owner, Future<bool> Function() confirm) {
    _owner = owner;
    _confirm = confirm;
  }

  void unbind(Object owner) {
    if (!identical(owner, _owner)) return;
    _owner = null;
    _confirm = null;
  }

  Future<bool> requestLeave() => _confirm?.call() ?? Future.value(true);
}

class SettingsPage extends StatefulWidget {
  const SettingsPage(
      {super.key,
      required this.controller,
      this.leaveGuard,
      this.onOpenManagement,
      this.onOpenGetStarted,
      this.onOpenVoice,
      this.onOpenDevices,
      this.installed = false,
      this.modelConfigured = false,
      this.voiceEnabled = false,
      this.themeMode = ThemeMode.dark,
      this.onToggleTheme});
  final DesktopSession controller;
  final SettingsLeaveGuard? leaveGuard;
  final VoidCallback? onOpenManagement,
      onOpenGetStarted,
      onOpenDevices,
      onOpenVoice;
  final bool installed, modelConfigured, voiceEnabled;
  final ThemeMode themeMode;
  final VoidCallback? onToggleTheme;
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final count = TextEditingController();
  late SettingField<int> field;
  late SettingsFormController<int> form;
  late Object boundApi;
  String section = SettingsSections.general;
  Future<bool>? _discardDecision;
  bool _profileDialogOpen = false;

  bool get changedConnection => !identical(boundApi, widget.controller.api);

  @override
  void initState() {
    super.initState();
    _bindForm();
    widget.leaveGuard?.bind(this, _requestLeave);
  }

  void _bindForm() {
    final controller = widget.controller, transport = widget.controller.api;
    boundApi = transport;
    field = SettingField<int>(
        source: 'connected service',
        applyTiming: 'new sessions',
        parse: int.tryParse,
        validate: (value) => value < 0 || value > 32
            ? 'Enter a whole number from 0 to 32.'
            : null);
    bool current() =>
        identical(controller, widget.controller) &&
        identical(transport, controller.api);
    form = SettingsFormController<int>(
        field: field,
        read: () async {
          if (!current()) throw StateError('Connection changed');
          final value = await controller.loadSettings();
          if (!current()) throw StateError('Connection changed');
          return (value['settings'] as Map)['max_consultations'] as int;
        },
        write: (value) async {
          if (!current()) throw StateError('Connection changed');
          await controller.updateConsultationLimit(value);
        });
    count.clear();
    form.addListener(_syncText);
    unawaited(form.load());
  }

  void _syncText() {
    if (count.text != field.text) count.text = field.text;
  }

  void _disposeForm() {
    form.removeListener(_syncText);
    form.dispose();
  }

  @override
  void didUpdateWidget(covariant SettingsPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.leaveGuard != widget.leaveGuard) {
      oldWidget.leaveGuard?.unbind(this);
      widget.leaveGuard?.bind(this, _requestLeave);
    }
    if (oldWidget.controller != widget.controller) {
      _disposeForm();
      _bindForm();
    }
  }

  @override
  void dispose() {
    widget.leaveGuard?.unbind(this);
    _disposeForm();
    count.dispose();
    super.dispose();
  }

  Future<bool> _requestLeave() async {
    if (form.loading || _profileDialogOpen) return false;
    final original = form;
    final allowed = await _confirmDiscard();
    return mounted && identical(form, original) && !form.loading && allowed;
  }

  Future<void> _editContextLimit(
      {bool rounds = false, bool enablement = false}) async {
    if (_profileDialogOpen) return;
    _profileDialogOpen = true;
    try {
      await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => CollaborationSettingDialog(
              session: widget.controller,
              rounds: rounds,
              enablement: enablement));
    } finally {
      _profileDialogOpen = false;
    }
  }

  Future<bool> _confirmDiscard() {
    if (!form.dirty) return Future.value(true);
    return _discardDecision ??=
        _showDiscard().whenComplete(() => _discardDecision = null);
  }

  Future<bool> _showDiscard() async {
    return await showDialog<bool>(
            context: context,
            builder: (context) => AlertDialog(
                  title: const Text('Discard unsaved changes?'),
                  content: const Text(
                      'Your collaboration limit has not been saved.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('Discard changes')),
                  ],
                )) ??
        false;
  }

  Future<void> _reload() async {
    if (form.loading || !await _confirmDiscard() || !mounted) return;
    if (changedConnection) {
      setState(() {
        _disposeForm();
        _bindForm();
      });
    } else {
      await form.load();
    }
  }

  Future<void> _open(VoidCallback callback) async {
    // In the app, the shell gates the outgoing callback once. Standalone pages
    // retain the existing local confirmation behavior.
    if (widget.leaveGuard != null) {
      if (!form.loading) callback();
      return;
    }
    if (!form.loading && await _confirmDiscard() && mounted) callback();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => ListenableBuilder(
            listenable: form,
            builder: (context, _) => LayoutBuilder(builder: (context, box) {
                  final compact = box.maxWidth < 800 ||
                      box.maxHeight < 600 ||
                      MediaQuery.textScalerOf(context).scale(14) > 20;
                  final inset =
                      box.maxWidth < 600 ? 16.0 : OdessyTokens.spaceLarge;
                  final header = Padding(
                      padding: EdgeInsets.all(inset), child: _header(context));
                  final content = Padding(
                      padding: EdgeInsets.all(inset), child: _content(context));
                  if (compact) {
                    return SingleChildScrollView(
                        key: const Key('settings-scroll'),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              header,
                              Padding(
                                  padding:
                                      EdgeInsets.symmetric(horizontal: inset),
                                  child: DropdownButtonFormField<String>(
                                    key: const Key('settings-section-picker'),
                                    initialValue: section,
                                    isExpanded: true,
                                    itemHeight: null,
                                    decoration: const InputDecoration(
                                        labelText: 'Settings section'),
                                    items: [
                                      for (final name in SettingsSections.all)
                                        DropdownMenuItem(
                                            value: name,
                                            child: Text(name,
                                                maxLines: 2,
                                                overflow:
                                                    TextOverflow.ellipsis))
                                    ],
                                    onChanged: (name) {
                                      if (name != null) {
                                        setState(() => section = name);
                                      }
                                    },
                                  )),
                              content,
                            ]));
                  }
                  return Column(children: [
                    header,
                    const Divider(height: 1),
                    Expanded(
                        child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                          SizedBox(
                              width: 232,
                              child: ListView(
                                  padding: const EdgeInsets.all(12),
                                  children: [
                                    for (final name in SettingsSections.all)
                                      Padding(
                                          padding:
                                              const EdgeInsets.only(bottom: 6),
                                          child: ListTile(
                                              key: ValueKey(
                                                  'settings-section-$name'),
                                              selected: section == name,
                                              selectedTileColor:
                                                  Theme.of(context)
                                                      .colorScheme
                                                      .primary
                                                      .withValues(alpha: .1),
                                              shape: RoundedRectangleBorder(
                                                  borderRadius:
                                                      BorderRadius.circular(
                                                          10)),
                                              leading:
                                                  Icon(_icon(name), size: 18),
                                              title: Text(name,
                                                  style: Theme.of(context)
                                                      .textTheme
                                                      .bodySmall),
                                              onTap: () => setState(
                                                  () => section = name))),
                                  ])),
                          const VerticalDivider(width: 1),
                          Expanded(
                              child: SingleChildScrollView(
                                  key: const Key('settings-scroll'),
                                  child: content)),
                        ])),
                  ]);
                })),
      );

  Widget _header(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Semantics(
            header: true,
            child: Text('Global settings',
                style: Theme.of(context).textTheme.titleLarge)),
        const SizedBox(height: 8),
        const Text(
            'Preferences for this app and its connected service. Device-specific controls stay on the device page.'),
        if (changedConnection) ...[
          const SizedBox(height: 12),
          const Text(
              'Connection changed. Reload settings before editing this service.'),
          Align(
              alignment: Alignment.centerLeft,
              child: OutlinedButton.icon(
                  onPressed: form.loading ? null : _reload,
                  icon: const Icon(Icons.refresh, size: 18),
                  label: const Text('Reload settings'))),
        ] else if (form.dirty && section != SettingsSections.models) ...[
          const SizedBox(height: 12),
          Wrap(
              spacing: 12,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                const Text('Unsaved collaboration changes'),
                TextButton(
                    onPressed: () =>
                        setState(() => section = SettingsSections.models),
                    child: const Text('Review changes'))
              ]),
        ],
      ]);

  IconData _icon(String name) => switch (name) {
        SettingsSections.general => Icons.tune_outlined,
        SettingsSections.models => Icons.hub_outlined,
        SettingsSections.voice => Icons.mic_none_outlined,
        SettingsSections.devices => Icons.devices_other_outlined,
        SettingsSections.installation => Icons.inventory_2_outlined,
        _ => Icons.code_outlined,
      };

  Widget _content(BuildContext context) => Align(
      alignment: Alignment.topLeft,
      child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 960),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(section, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 16),
            ...switch (section) {
              SettingsSections.general => _general(context),
              SettingsSections.models => _models(context),
              SettingsSections.voice => _voice(context),
              SettingsSections.devices => _devices(context),
              SettingsSections.advanced => _advanced(context),
              _ => _installation(context),
            },
          ])));

  List<Widget> _general(BuildContext context) => [
        _card(context, 'Appearance', Icons.contrast, [
          _detail(
              context,
              'Current theme',
              switch (widget.themeMode) {
                ThemeMode.dark => 'Dark',
                ThemeMode.light => 'Light',
                _ => 'System',
              }),
          const Text('Changes the appearance of this window.'),
          const SizedBox(height: 12),
          Align(
              alignment: Alignment.centerLeft,
              child: OutlinedButton.icon(
                  onPressed: widget.onToggleTheme,
                  icon: const Icon(Icons.contrast, size: 18),
                  label: Text(widget.themeMode == ThemeMode.dark
                      ? 'Use light theme'
                      : 'Use dark theme'))),
        ]),
        _card(context, 'Connected service', Icons.cable_outlined, [
          _detail(
              context,
              'Service',
              widget.controller.api.isDemo
                  ? 'Demo session'
                  : widget.controller.api.identity),
          _detail(
              context,
              'Connection',
              widget.controller.busy && widget.controller.session == null
                  ? 'Connecting'
                  : widget.controller.connected
                      ? 'Connected'
                      : 'Offline'),
          _detail(
              context,
              'Installation',
              widget.installed
                  ? 'Managed installation'
                  : 'Not managed by this app'),
          if (widget.controller.api.isDemo)
            const Text('Demo settings are kept in memory only.'),
        ]),
      ];

  List<Widget> _models(BuildContext context) => [
        _card(context, 'Session profiles', Icons.view_list_outlined, [
          _detail(
              context,
              'Next turn',
              widget.controller.connected && !changedConnection
                  ? widget.controller.session?.selectedProfile ?? 'Not reported'
                  : 'Unavailable'),
          _detail(
              context,
              'Profiles loaded',
              !widget.controller.connected || changedConnection
                  ? 'Unavailable'
                  : widget.controller.profiles.isEmpty
                      ? 'None reported'
                      : widget.controller.profiles.join('\n')),
          const Text(
              'Profiles are loaded when the session starts and may have changed. Select the next-turn profile in the conversation.'),
          if (widget.controller.api.isDemo)
            const Text('These profiles belong to the demo session.'),
        ]),
        _card(context, 'Model configuration', Icons.hub_outlined, [
          _detail(
              context,
              'Recorded configuration',
              !widget.installed
                  ? 'Not reported'
                  : widget.modelConfigured
                      ? 'Configured'
                      : 'Not configured'),
          const Text(
              'Open configuration to review your model and connection. Opening this page does not start a model.'),
          const SizedBox(height: 12),
          _entry(
              widget.modelConfigured ? 'Change model' : 'Configure a model',
              widget.onOpenGetStarted,
              'Model configuration is available from a managed installation.'),
        ]),
        ModelProfiles(
            session: widget.controller,
            onDialogChange: (open) => _profileDialogOpen = open),
        _card(context, 'Collaboration', Icons.forum_outlined, [
          _collaborationForm(context),
          OutlinedButton(
              onPressed: widget.controller.connected &&
                      !changedConnection &&
                      widget.controller.api is CollaborationEnabledApi &&
                      !widget.controller.api.isDemo
                  ? () => _editContextLimit(enablement: true)
                  : null,
              child: const Text('Edit collaboration availability')),
          OutlinedButton(
              onPressed: widget.controller.connected &&
                      !changedConnection &&
                      widget.controller.api is ContextLimitApi &&
                      !widget.controller.api.isDemo
                  ? () => _editContextLimit()
                  : null,
              child: const Text('Edit context limit')),
          OutlinedButton(
              onPressed: widget.controller.connected &&
                      !changedConnection &&
                      widget.controller.api is RoundLimitApi &&
                      !widget.controller.api.isDemo
                  ? () => _editContextLimit(rounds: true)
                  : null,
              child: const Text('Edit round limit'))
        ]),
      ];

  List<Widget> _voice(BuildContext context) => [
        _card(context, 'Voice configuration', Icons.mic_none_outlined, [
          _detail(
              context,
              'Recorded assets',
              !widget.installed
                  ? 'Not reported'
                  : widget.voiceEnabled
                      ? 'Existing assets recorded'
                      : 'Not configured'),
          const Text(
              'Review your existing voice components and their configuration.'),
          const SizedBox(height: 12),
          _entry(
              'Open configuration',
              widget.onOpenVoice ?? widget.onOpenGetStarted,
              'Voice configuration is available from a managed installation.'),
        ]),
        _card(context, 'Desktop audio', Icons.headset_outlined, [
          _detail(context, 'Microphone controls', 'Conversation'),
          const Text(
              'Start or stop voice from Conversation when supported by the local service.'),
        ]),
      ];

  List<Widget> _devices(BuildContext context) => [
        _card(context, 'Device settings', Icons.devices_other_outlined, [
          const Text(
              'Select a device to inspect its connection, access and available settings.'),
          const SizedBox(height: 12),
          _entry('Open devices', widget.onOpenDevices,
              'The device page is unavailable in this context.'),
        ]),
        _card(context, 'Tool access', Icons.shield_outlined, [
          _detail(context, 'Permission editing', 'Unavailable'),
          const Text(
              'Pairing and workspace permissions are managed outside this page. Selecting a device does not grant tool access.'),
        ]),
      ];

  List<Widget> _advanced(BuildContext context) => [
        RuntimeInventory(session: widget.controller),
        _card(context, 'Runtime configuration', Icons.code_outlined, [
          const Text(
              'Configuration editing is not available here. Existing runtime configuration remains managed outside this page.'),
          const SizedBox(height: 16),
          for (final label in [
            'Provider credentials',
            'Tool servers',
            'Runtime policy',
            'Resource limits'
          ])
            _detail(context, label, 'Managed outside this page'),
        ]),
      ];

  List<Widget> _installation(BuildContext context) => [
        _card(context, 'Installation management', Icons.inventory_2_outlined, [
          _detail(
              context,
              'Installed copy',
              widget.installed
                  ? 'Managed installation'
                  : 'Not managed by this app'),
          const Text(
              'Review repair, update, rollback and uninstall options in installation management.'),
          const SizedBox(height: 12),
          _entry('Manage installation', widget.onOpenManagement,
              'No managed installation is available in this context.'),
        ]),
      ];

  Widget _entry(String label, VoidCallback? callback, String unavailable) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        OutlinedButton(
            onPressed:
                callback == null || form.loading ? null : () => _open(callback),
            child: Text(label)),
        if (callback == null)
          Padding(
              padding: const EdgeInsets.only(top: 8), child: Text(unavailable)),
      ]);

  Widget _card(BuildContext context, String title, IconData icon,
          List<Widget> children) =>
      Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: Card(
              child: Padding(
                  padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Icon(icon,
                                  size: 20,
                                  color: Theme.of(context)
                                      .colorScheme
                                      .onSurfaceVariant),
                              const SizedBox(width: 8),
                              Expanded(
                                  child: Text(title,
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium)),
                            ]),
                        const SizedBox(height: 16),
                        ...children,
                      ]))));

  Widget _detail(BuildContext context, String label, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: LayoutBuilder(builder: (context, box) {
        final heading =
            Text(label, style: Theme.of(context).textTheme.bodySmall);
        final content = SelectableText(value);
        if (box.maxWidth < 460) {
          return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [heading, const SizedBox(height: 4), content]);
        }
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 168, child: heading),
          const SizedBox(width: 12),
          Expanded(child: content),
        ]);
      }));

  Widget _collaborationForm(BuildContext context) {
    final state = changedConnection
        ? 'Connection changed'
        : form.loading
            ? 'Loading settings…'
            : form.error != null
                ? 'Settings need attention'
                : !form.loaded
                    ? 'Settings unavailable'
                    : field.dirty
                        ? field.error != null
                            ? 'Invalid value'
                            : 'Unsaved changes'
                        : form.status.isNotEmpty
                            ? 'Saved'
                            : 'Up to date';
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(state, style: Theme.of(context).textTheme.labelLarge),
      const SizedBox(height: 8),
      const Text(
          'Set the maximum consultations per turn. Changing this limit does not enable collaboration.'),
      const SizedBox(height: 16),
      _detail(context, 'Scope', 'Connected service'),
      _detail(context, 'Applies to', 'New sessions'),
      const SizedBox(height: 12),
      Align(
          alignment: Alignment.centerLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 320),
            child: TextField(
                key: const Key('consultation-limit'),
                controller: count,
                onChanged: form.edit,
                enabled: form.loaded && !form.loading && !changedConnection,
                keyboardType:
                    const TextInputType.numberWithOptions(signed: true),
                decoration: InputDecoration(
                    labelText: 'Consultation limit',
                    helperText: 'Whole number from 0 to 32',
                    helperMaxLines: 3,
                    errorMaxLines: 3,
                    errorText: field.dirty ? field.error : null)),
          )),
      const SizedBox(height: 16),
      Wrap(spacing: 10, runSpacing: 8, children: [
        FilledButton(
            onPressed: form.canSave && !changedConnection && form.error == null
                ? () => form.save(
                    successMessage: widget.controller.api.isDemo
                        ? 'Saved in demo memory only.'
                        : 'Saved by the service. Applies to newly created sessions.')
                : null,
            child: const Text('Save settings')),
        OutlinedButton(
            onPressed: form.loading ? null : _reload,
            child: const Text('Reload')),
      ]),
      if (form.loading)
        const Padding(
            padding: EdgeInsets.only(top: 16),
            child: LinearProgressIndicator(
                semanticsLabel: 'Loading or saving collaboration settings')),
      if (form.error != null || form.status.isNotEmpty)
        Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Semantics(
                liveRegion: true, child: Text(form.error ?? form.status))),
      const SizedBox(height: 16),
      Text('Existing sessions retain their configuration.',
          style: Theme.of(context).textTheme.bodySmall),
    ]);
  }
}
