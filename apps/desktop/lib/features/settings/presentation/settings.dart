import '../../../widgets/settings_scaffold.dart';
import '../../../app/ux_copy.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/setup/native_picker.dart';
import '../controllers/component_settings_controller.dart';
import 'component_settings_pane.dart';
import 'settings_navigation.dart';
import 'model_profiles.dart';
import 'runtime_inventory.dart';
import 'collaboration_setting_dialog.dart';
import '../../../app/readiness_controller.dart';
import 'package:flutter/material.dart';

import '../../../app/session_controller.dart';
import '../../../app/theme.dart';
import '../controllers/setting_field.dart';
import '../controllers/settings_form_controller.dart';

/// Settings grouped by what a person is trying to change.
///
/// Only capabilities with a reviewed backend operation are editable. Everything
/// else is shown as read-only or unavailable instead of being silently dropped.
class SettingsSections {
  static const general = 'General';
  static const models = 'Models and connections';
  static const voice = 'Voice';
  static const stt = 'Speech to text (STT)';
  static const tts = 'Text to speech (TTS)';
  static const devices = 'Devices and tool access';
  static const advanced = 'Advanced';
  static const installation = 'Installation';
  static const all = [
    general,
    models,
    stt,
    tts,
    voice,
    devices,
    advanced,
    installation
  ];
}

class SettingsPage extends StatefulWidget {
  const SettingsPage({
    super.key,
    required this.controller,
    this.onOpenManagement,
    this.onOpenInstaller,
    this.onOpenMaintenance,
    this.onConnectSaved,
    this.maintenanceBlocked = false,
    this.configurationBackend,
    this.componentController,
    this.readiness,
    this.onRestartSaved,
    this.formController,
    this.installationRoot = '',
    this.initialSection = SettingsSections.general,
    this.onOpenGetStarted,
    this.onOpenVoice,
    this.onOpenDevices,
    this.onOpenConversation,
    this.installed = false,
    this.modelConfigured = false,
    this.voiceEnabled = false,
    this.themeMode = ThemeMode.dark,
    this.onToggleTheme,
  });

  final DesktopSession controller;
  final SetupBackend? configurationBackend;
  final ComponentSettingsController? componentController;
  final ReadinessController? readiness;
  final VoidCallback? onRestartSaved;
  final SettingsFormController<int>? formController;
  final String installationRoot, initialSection;
  final VoidCallback? onOpenManagement, onOpenGetStarted, onOpenDevices;
  final VoidCallback? onOpenConversation;
  final VoidCallback? onOpenVoice;
  final VoidCallback? onOpenInstaller, onOpenMaintenance;
  final bool maintenanceBlocked;
  final VoidCallback? onConnectSaved;
  final bool installed, modelConfigured, voiceEnabled;
  final ThemeMode themeMode;
  final VoidCallback? onToggleTheme;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final count = TextEditingController();
  final _modelSettingsKey = GlobalKey();
  final _modelChecksKey = GlobalKey();
  final _modelProfilesKey = GlobalKey();
  final fallbackField = SettingField<int>(
      source: 'user collaboration configuration',
      applyTiming: 'new sessions',
      parse: int.tryParse,
      validate: (v) =>
          v < 0 || v > 32 ? 'Enter a whole number from 0 to 32.' : null);
  late final SettingsFormController<int> form;
  late OdessyApi _formApi;
  bool get changedConnection => !identical(_formApi, widget.controller.api);
  SettingField<int> get field => form.field;
  String section = SettingsSections.general;
  ComponentSettingsController? components;
  bool changingSection = false;
  bool _dialogOpen = false;

  bool get loading => form.loading;
  String get status => form.error ?? form.status;

  @override
  void initState() {
    super.initState();
    _formApi = widget.controller.api;
    widget.controller.addListener(_sessionChanged);
    section = widget.initialSection;
    if (widget.componentController != null) {
      components = widget.componentController;
    } else if (widget.configurationBackend != null) {
      components = ComponentSettingsController(widget.configurationBackend!,
          root: widget.installationRoot);
    }
    form = widget.formController ??
        SettingsFormController<int>(
            field: fallbackField,
            read: () async {
              final transport = widget.controller.api;
              if (changedConnection) throw StateError('Connection changed');
              final value = await widget.controller.loadSettings();
              if (!identical(transport, widget.controller.api)) {
                throw StateError('Connection changed');
              }
              return (value['settings'] as Map)['max_consultations'] as int;
            },
            write: (value) async {
              if (changedConnection) throw StateError('Connection changed');
              await widget.controller.updateConsultationLimit(value);
            });
    form.addListener(syncText);
    if (!form.loaded && !form.loading) form.load();
    syncText();
  }

  void syncText() {
    if (count.text != field.text) count.text = field.text;
  }

  void _sessionChanged() {
    if (mounted) setState(() {});
  }

  Future<void> load() async {
    if (form.dirty) {
      final discard = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                  title: const Text('Discard unsaved changes?'),
                  content: const Text('Your current edit will be discarded.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('Discard changes'))
                  ]));
      if (discard != true || !mounted) return;
    }
    if (changedConnection && widget.formController == null) {
      _formApi = widget.controller.api;
    }
    await form.load();
  }

  Future<void> save() => form.save(
      successMessage: widget.controller.api.isDemo
          ? 'Saved in demo memory only.'
          : 'Saved by the service. Applies to newly created sessions.');

  Future<void> _editCollaboration(
      {bool rounds = false, bool enablement = false}) async {
    if (_dialogOpen || changedConnection) return;
    _dialogOpen = true;
    try {
      await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => CollaborationSettingDialog(
              session: widget.controller,
              rounds: rounds,
              enablement: enablement));
    } finally {
      _dialogOpen = false;
    }
  }

  Future<void> _openStandalone(VoidCallback action) async {
    if (form.loading) return;
    if (widget.formController != null) {
      action();
      return;
    }
    if (form.dirty) {
      final discard = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                title: const Text('Discard unsaved changes?'),
                content:
                    const Text('Your collaboration limit has not been saved.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Discard changes')),
                ],
              ));
      if (discard != true || !mounted) return;
      await form.load();
      if (!mounted || form.error != null) return;
    }
    action();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_sessionChanged);
    form.removeListener(syncText);
    if (widget.formController == null) form.dispose();
    if (widget.componentController == null) components?.dispose();
    count.dispose();
    super.dispose();
  }

  Widget _rail(BuildContext context) => SizedBox(
      width: 236,
      child: ListView(shrinkWrap: true, children: [
        for (final name in SettingsSections.all)
          ListTile(
              key: ValueKey('settings-section-$name'),
              contentPadding: const EdgeInsets.symmetric(horizontal: 16),
              selected: section == name,
              title: Text(name, style: const TextStyle(fontSize: 13)),
              onTap: () => _selectSection(name)),
      ]));

  Widget _narrowPicker(BuildContext context) => KeyedSubtree(
      key: const Key('settings-section-picker'),
      child: DropdownButtonFormField<String>(
          key: ValueKey('settings-section-picker-$section'),
          initialValue: section,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Settings section'),
          items: [
            for (final name in SettingsSections.all)
              DropdownMenuItem(
                  value: name,
                  child: Text(name, overflow: TextOverflow.ellipsis))
          ],
          onChanged: (name) {
            if (name != null) _selectSection(name);
          }));

  Future<void> _selectSection(String name) async {
    if (name == section || changingSection || _dialogOpen || form.loading) {
      return;
    }
    changingSection = true;
    try {
      if (!await confirmSettingsNavigation(context, components) || !mounted) {
        return;
      }
      setState(() => section = name);
    } finally {
      changingSection = false;
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: Listenable.merge([
        form,
        if (components != null) components!,
        if (widget.readiness != null) widget.readiness!
      ]),
      builder: (context, _) => SettingsScaffold(
          title: DesktopCopy.settingsTitle,
          description: DesktopCopy.settingsDescription,
          navigation: _rail(context),
          compactNavigation: _narrowPicker(context),
          child: _content(context)));

  Widget _component(String name) => components == null
      ? const Text('Local configuration is unavailable for this connection.')
      : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (widget.readiness != null) ...[
            Text(
                widget.readiness!.label(name, components!.snapshot,
                    connected: widget.controller.connected),
                key: ValueKey('configuration-state-$name')),
            const SizedBox(height: 8),
            if (name == 'llm')
              Text(
                  'Startup model: ${widget.readiness!.current?.configuration?.modelName ?? 'unknown'} · Saved model: ${((components!.snapshot['selection'] as Map?)?['model'] as Map?)?['name'] ?? 'not configured'}'),
            if (widget.readiness!.error.isNotEmpty)
              Text(widget.readiness!.error),
            Wrap(spacing: 8, children: [
              TextButton(
                  onPressed: () =>
                      widget.readiness!.bind(widget.controller.api),
                  child: const Text('Refresh running state')),
              if (widget.onRestartSaved != null)
                TextButton(
                    onPressed: components!.busy ? null : widget.onRestartSaved,
                    child: const Text('Apply saved settings · restart')),
            ]),
            if (widget.onRestartSaved == null && widget.controller.connected)
              const Text(
                  'Restart through the service owner to apply saved settings. This desktop cannot restart an unowned or unverified service.'),
          ],
          ComponentSettingsPane(
              key: ValueKey(name),
              controller: components!,
              section: name,
              checksKey: name == 'llm' ? _modelChecksKey : null,
              picker: const FileSelectorPicker()),
        ]);

  void _showModelSection(GlobalKey target, {GlobalKey? fallback}) {
    final element = target.currentContext ?? fallback?.currentContext;
    if (element == null) return;
    Scrollable.ensureVisible(element,
        duration: MediaQuery.disableAnimationsOf(context)
            ? Duration.zero
            : const Duration(milliseconds: 250),
        curve: Curves.easeOut);
  }

  Widget _content(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        KeyedSubtree(
          key: const ValueKey('collaboration-draft-notice'),
          child: form.dirty && section != SettingsSections.models
              ? _card(context, 'Unsaved collaboration changes', [
                  TextButton(
                      onPressed: () => _selectSection(SettingsSections.models),
                      child: const Text('Review changes')),
                ])
              : const SizedBox.shrink(),
        ),
        if (section == SettingsSections.general) ...[
          _card(context, 'Connection', [
            _row(
                'Local service',
                widget.controller.api.isDemo
                    ? 'Demo (not installed)'
                    : widget.controller.connected
                        ? 'Connected'
                        : 'Not connected'),
            _row('State', widget.controller.status),
            _row(
                'Installation',
                widget.installed
                    ? 'Managed installation'
                    : 'Not managed by this app'),
            if (widget.onToggleTheme != null)
              Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                      onPressed: widget.onToggleTheme,
                      icon: const Icon(Icons.contrast, size: 18),
                      label: Text(widget.themeMode == ThemeMode.dark
                          ? 'Use light theme'
                          : 'Use dark theme'))),
          ]),
          _card(context, 'Collaboration', [_collaborationForm(context)]),
        ],
        if (section == SettingsSections.models) ...[
          _card(context, 'Set up a model', [
            const Text(
                '1. Configure the startup model, or add a session profile.'),
            const SizedBox(height: 8),
            Text(components == null
                ? '2. Model checks are unavailable for this connection.'
                : '2. Check the configuration or run a model test below. A model test asks for confirmation and cloud usage may be billed.'),
            const SizedBox(height: 8),
            const Text(
                '3. Apply saved startup changes as indicated. Return to Assistant and choose the model for the next turn. New profiles are available to new sessions.'),
            const SizedBox(height: 12),
            Wrap(spacing: 8, runSpacing: 8, children: [
              OutlinedButton(
                  onPressed: () => _showModelSection(_modelSettingsKey),
                  child: const Text('Startup model')),
              if (components != null)
                OutlinedButton(
                    onPressed: () => _showModelSection(_modelChecksKey,
                        fallback: _modelSettingsKey),
                    child: const Text('Model checks')),
              OutlinedButton(
                  onPressed: () => _showModelSection(_modelProfilesKey),
                  child: const Text('Saved profiles')),
              if (widget.onOpenConversation != null)
                TextButton(
                    onPressed: widget.onOpenConversation,
                    child: const Text('Return to Assistant')),
            ]),
          ]),
          _card(context, 'Session profiles', [
            _row('Coordinator',
                widget.controller.connected ? 'Connected' : 'Offline'),
            _row(
                'Next-turn model',
                widget.controller.connected
                    ? widget.controller.session?.selectedProfile ??
                        'Unavailable'
                    : 'Unavailable'),
            _row(
                'Available profiles',
                widget.controller.connected
                    ? widget.controller.profiles.join('\n')
                    : 'Unavailable'),
            if (widget.controller.api.isDemo)
              const Text('These profiles belong to the demo session.'),
            const Text(
                'A connected coordinator does not confirm that the selected model is available.'),
          ]),
          KeyedSubtree(
              key: _modelProfilesKey,
              child: ModelProfiles(
                  session: widget.controller,
                  onDialogChange: (open) => _dialogOpen = open)),
          const Text(
              'Save your API key securely below, or use it for this session only. Environment-provided keys remain supported and read-only.'),
          KeyedSubtree(
              key: _modelSettingsKey,
              child: components != null
                  ? _component('llm')
                  : _card(context, 'Model', [
                      _row(
                          'State',
                          widget.modelConfigured
                              ? 'Configured'
                              : 'Not reported'),
                      const SizedBox(height: OdessyTokens.spaceCompact),
                      const Text(
                          'Choosing a model and endpoint is a reviewed configuration change. It does not start the model or contact a provider.',
                          style: TextStyle(fontSize: 12)),
                      Align(
                          alignment: Alignment.centerLeft,
                          child: OutlinedButton(
                              onPressed: widget.onOpenGetStarted == null
                                  ? null
                                  : () =>
                                      _openStandalone(widget.onOpenGetStarted!),
                              child: Text(widget.modelConfigured
                                  ? 'Change model'
                                  : 'Configure a model'))),
                    ])),
          if (widget.onConnectSaved != null)
            Padding(
                padding: const EdgeInsets.only(bottom: 24),
                child: Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton.icon(
                        onPressed: widget.onConnectSaved,
                        icon: const Icon(Icons.play_arrow_outlined),
                        label: const Text('Connect saved configuration')))),
          _card(context, 'Collaboration', [
            _collaborationForm(context),
            OutlinedButton(
                onPressed: widget.controller.connected &&
                        !changedConnection &&
                        widget.controller.api is CollaborationEnabledApi &&
                        !widget.controller.api.isDemo
                    ? () => _editCollaboration(enablement: true)
                    : null,
                child: const Text('Edit collaboration availability')),
            OutlinedButton(
                onPressed: widget.controller.connected &&
                        !changedConnection &&
                        widget.controller.api is ContextLimitApi &&
                        !widget.controller.api.isDemo
                    ? () => _editCollaboration()
                    : null,
                child: const Text('Edit context limit')),
            OutlinedButton(
                onPressed: widget.controller.connected &&
                        !changedConnection &&
                        widget.controller.api is RoundLimitApi &&
                        !widget.controller.api.isDemo
                    ? () => _editCollaboration(rounds: true)
                    : null,
                child: const Text('Edit round limit')),
          ]),
        ],
        if (section == SettingsSections.stt) _component('stt'),
        if (section == SettingsSections.tts) _component('tts'),
        if (section == SettingsSections.voice)
          if (components != null)
            _component('voice')
          else
            _card(context, 'Voice', [
              _row(
                  'State',
                  widget.voiceEnabled
                      ? 'Existing assets recorded'
                      : 'Not configured'),
              const Text(
                  'Voice is optional and uses components you already have. It is not downloaded or started here.',
                  style: TextStyle(fontSize: 12)),
              if (widget.onOpenVoice != null || widget.onOpenGetStarted != null)
                Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton(
                        onPressed: () => _openStandalone(
                            (widget.onOpenVoice ?? widget.onOpenGetStarted)!),
                        child: const Text('Open configuration'))),
            ]),
        if (section == SettingsSections.devices && components != null)
          _component('host'),
        if (section == SettingsSections.devices)
          _card(context, 'Devices and tool access', [
            const Text(
                'Execution hosts, workspace grants and pairing are separate from model settings.',
                style: TextStyle(fontSize: 12)),
            if (widget.onOpenDevices != null)
              Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                      onPressed: widget.onOpenDevices,
                      icon: const Icon(Icons.devices_other, size: 18),
                      label: const Text('Open devices'))),
          ]),
        if (section == SettingsSections.advanced)
          RuntimeInventory(session: widget.controller),
        if (section == SettingsSections.advanced)
          _card(context, 'Advanced', [
            const Text(
                'Advanced fields are grouped and read-only until a reviewed editing contract exists. '
                'They remain in the environment configuration and are never lost by this page.',
                style: TextStyle(fontSize: 12)),
            const SizedBox(height: OdessyTokens.spaceCompact),
            const Text(
                'Read-only here: provider keys, MCP servers, usage limits, sandbox modes, service definitions.',
                style: TextStyle(fontSize: 11, color: Colors.grey)),
          ]),
        if (section == SettingsSections.installation)
          _card(context, 'Installation', [
            if (widget.maintenanceBlocked)
              const Text(
                  'Finish or stop the current task before starting installation or maintenance.'),
            if (widget.onOpenInstaller != null)
              OutlinedButton.icon(
                  onPressed:
                      widget.maintenanceBlocked ? null : widget.onOpenInstaller,
                  icon: const Icon(Icons.download_outlined, size: 18),
                  label: const Text('Install Odessy')),
            if (widget.onOpenMaintenance != null)
              OutlinedButton.icon(
                  onPressed: widget.maintenanceBlocked
                      ? null
                      : widget.onOpenMaintenance,
                  icon: const Icon(Icons.handyman_outlined, size: 18),
                  label: const Text('Runtime maintenance')),
            _row('Installed copy',
                widget.installed ? 'Yes' : 'Not managed by this app'),
            const Text(
                'Repair, install a newer local package, roll back, change configuration or uninstall. '
                'Maintenance runs outside the installation.',
                style: TextStyle(fontSize: 12)),
            if (widget.onOpenManagement != null)
              Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                      onPressed: widget.onOpenManagement,
                      icon: const Icon(Icons.settings_outlined, size: 18),
                      label: const Text('Manage installation'))),
          ]),
      ]);

  Widget _card(BuildContext context, String title, List<Widget> children) =>
      Padding(
          padding: const EdgeInsets.only(bottom: OdessyTokens.spaceLarge),
          child: Card(
              child: Padding(
                  padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title,
                            style: const TextStyle(
                                fontSize: 15, fontWeight: FontWeight.w600)),
                        const SizedBox(height: OdessyTokens.spaceMedium),
                        ...children,
                      ]))));

  Widget _row(String label, String value) => Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
            width: 120,
            child: Text(label, style: const TextStyle(fontSize: 12))),
        Expanded(child: Text(value, style: const TextStyle(fontSize: 12))),
      ]));

  Widget _collaborationForm(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('Maximum consultations per turn',
            style: TextStyle(fontSize: 13)),
        if (changedConnection)
          const Text('Connection changed. Reload settings before editing.'),
        const SizedBox(height: 8),
        const Text(
            'Choose how often Odessy may consult another configured model. This limit does not enable collaboration by itself.',
            style: TextStyle(fontSize: 11, color: Colors.grey)),
        const SizedBox(height: 20),
        SizedBox(
            width: 240,
            child: TextField(
                key: const Key('consultation-limit'),
                controller: count,
                onChanged: (value) => form.edit(value),
                enabled: !loading && form.loaded && !changedConnection,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                    labelText: 'Limit · 0–32',
                    errorText: field.error,
                    helperText: form.inspectionRequired
                        ? 'Reload saved values before another save.'
                        : null))),
        const SizedBox(height: 18),
        Wrap(spacing: 10, children: [
          FilledButton(
              onPressed: form.canSave && !changedConnection ? save : null,
              child: const Text('Save settings')),
          OutlinedButton(
              onPressed: loading ? null : load,
              child: Text(changedConnection ? 'Reload settings' : 'Reload'))
        ]),
        if (loading)
          const Padding(
              padding: EdgeInsets.only(top: 16),
              child: LinearProgressIndicator()),
        if (status.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: 16),
              child: Text(status, style: const TextStyle(fontSize: 12))),
        const SizedBox(height: 16),
        const Text(
            'Applies to new conversations. Current tasks keep their existing settings.',
            style: TextStyle(fontSize: 11, color: Colors.grey)),
      ]);
}
