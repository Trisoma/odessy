import '../../../widgets/settings_scaffold.dart';
import '../../../app/ux_copy.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/setup/native_picker.dart';
import '../controllers/component_settings_controller.dart';
import 'component_settings_pane.dart';
import 'settings_navigation.dart';
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
    this.formController,
    this.installationRoot = '',
    this.initialSection = SettingsSections.general,
    this.onOpenGetStarted,
    this.onOpenVoice,
    this.onOpenDevices,
    this.installed = false,
    this.modelConfigured = false,
    this.voiceEnabled = false,
    this.themeMode = ThemeMode.dark,
    this.onToggleTheme,
  });

  final DesktopSession controller;
  final SetupBackend? configurationBackend;
  final ComponentSettingsController? componentController;
  final SettingsFormController<int>? formController;
  final String installationRoot, initialSection;
  final VoidCallback? onOpenManagement, onOpenGetStarted, onOpenDevices;
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
  final fallbackField = SettingField<int>(
      source: 'user collaboration configuration',
      applyTiming: 'new sessions',
      parse: int.tryParse,
      validate: (v) =>
          v < 0 || v > 32 ? 'Enter a whole number from 0 to 32.' : null);
  late final SettingsFormController<int> form;
  SettingField<int> get field => form.field;
  String section = SettingsSections.general;
  ComponentSettingsController? components;
  bool changingSection = false;

  bool get loading => form.loading;
  String get status => form.error ?? form.status;

  @override
  void initState() {
    super.initState();
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
              final value = await widget.controller.loadSettings();
              if (!identical(transport, widget.controller.api)) {
                throw StateError('Connection changed');
              }
              return (value['settings'] as Map)['max_consultations'] as int;
            },
            write: widget.controller.updateConsultationLimit);
    form.addListener(syncText);
    if (!form.loaded && !form.loading) form.load();
    syncText();
  }

  void syncText() {
    if (count.text != field.text) count.text = field.text;
  }

  Future<void> load() async {
    if (form.dirty) {
      final discard = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                  title: const Text('Reload saved collaboration setting?'),
                  content: const Text('Your current edit will be discarded.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Keep editing')),
                    TextButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('Discard and reload'))
                  ]));
      if (discard != true || !mounted) return;
    }
    await form.load();
  }

  Future<void> save() => form.save(
      successMessage: widget.controller.api.isDemo
          ? 'Saved in demo memory only.'
          : 'Saved by the service. Applies to newly created sessions.');

  @override
  void dispose() {
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

  Widget _narrowPicker(BuildContext context) => DropdownButtonFormField<String>(
      key: ValueKey('settings-section-picker-$section'),
      initialValue: section,
      isExpanded: true,
      decoration: const InputDecoration(labelText: 'Settings section'),
      items: [
        for (final name in SettingsSections.all)
          DropdownMenuItem(
              value: name, child: Text(name, overflow: TextOverflow.ellipsis))
      ],
      onChanged: (name) {
        if (name != null) _selectSection(name);
      });

  Future<void> _selectSection(String name) async {
    if (name == section || changingSection) return;
    changingSection = true;
    try {
      if (!await confirmSettingsNavigation(context, components) || !mounted) {
        return;
      }
      if (!await confirmFormNavigation(context, form) || !mounted) return;
      setState(() => section = name);
    } finally {
      changingSection = false;
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: form,
      builder: (context, _) => SettingsScaffold(
          title: DesktopCopy.settingsTitle,
          description: DesktopCopy.settingsDescription,
          navigation: _rail(context),
          compactNavigation: _narrowPicker(context),
          child: _content(context)));

  Widget _component(String name) => components == null
      ? const Text('Local configuration is unavailable for this connection.')
      : ComponentSettingsPane(
          key: ValueKey(name),
          controller: components!,
          section: name,
          picker: const FileSelectorPicker());

  Widget _content(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
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
          if (components != null)
            _component('llm')
          else
            _card(context, 'Model', [
              _row('State',
                  widget.modelConfigured ? 'Configured' : 'Not configured'),
              const SizedBox(height: OdessyTokens.spaceCompact),
              const Text(
                  'Choosing a model and endpoint is a reviewed configuration change. It does not start the model or contact a provider.',
                  style: TextStyle(fontSize: 12)),
              if (widget.onOpenGetStarted != null)
                Align(
                    alignment: Alignment.centerLeft,
                    child: FilledButton.tonal(
                        onPressed: widget.onOpenGetStarted,
                        child: Text(widget.modelConfigured
                            ? 'Change model'
                            : 'Configure a model'))),
            ]),
          if (widget.onConnectSaved != null)
            Padding(
                padding: const EdgeInsets.only(bottom: 24),
                child: Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton.icon(
                        onPressed: widget.onConnectSaved,
                        icon: const Icon(Icons.play_arrow_outlined),
                        label: const Text('Connect saved configuration')))),
          _card(context, 'Collaboration', [_collaborationForm(context)]),
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
              if (widget.onOpenGetStarted != null)
                Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton(
                        onPressed:
                            widget.onOpenVoice ?? widget.onOpenGetStarted,
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
                enabled: !loading && form.loaded,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(labelText: 'Limit · 0–32',
                    errorText: field.error,
                    helperText: form.inspectionRequired ? 'Reload saved values before another save.' : null))),
        const SizedBox(height: 18),
        Wrap(spacing: 10, children: [
          FilledButton(
              onPressed: form.canSave ? save : null,
              child: const Text('Save settings')),
          OutlinedButton(
              onPressed: loading ? null : load, child: const Text('Reload'))
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
