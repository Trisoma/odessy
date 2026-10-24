import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../../app/setup/native_picker.dart';
import '../../../app/theme.dart';
import '../controllers/installed_manage_controller.dart';
import 'path_field.dart';
import 'setup_shell.dart';

/// First-run configuration: installing files is done; this is getting ready to chat.
///
/// Model choice is explicit and reversible (On this computer / Remote or cloud /
/// Configure later). Nothing contacts a provider until the user asks, nothing is
/// downloaded, and no credential is entered or stored here.
class GetStartedPage extends StatefulWidget {
  const GetStartedPage({
    super.key,
    required this.controller,
    required this.onStart,
    this.onClose,
    this.picker = const UnavailablePicker(),
    this.coordinatorReady = false,
  });

  final InstalledManageController controller;
  final VoidCallback onStart;
  final VoidCallback? onClose;
  final NativePicker picker;
  final bool coordinatorReady;

  @override
  State<GetStartedPage> createState() => _GetStartedPageState();
}

class _GetStartedPageState extends State<GetStartedPage> {
  final endpoint = TextEditingController(text: 'http://127.0.0.1:11434');
  final model = TextEditingController();
  final workspace = TextEditingController();
  final credential = TextEditingController();
  String choice = 'local', preset = 'openai';
  bool initialised = false;

  InstalledManageController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_refresh);
    if (c.status.isNotEmpty) _fromStatus();
  }

  void _fromStatus() {
    if (initialised) return;
    initialised = true;
    workspace.text = c.workspace;
    if (c.modelConfigured) choice = 'done';
  }

  void _refresh() {
    if (!mounted) return;
    _fromStatus();
    setState(() {});
  }

  @override
  void dispose() {
    c.removeListener(_refresh);
    endpoint.dispose();
    model.dispose();
    workspace.dispose();
    credential.dispose();
    super.dispose();
  }

  Json get _selection => {
        'role': c.role,
        if (choice == 'local')
          'model': {
            'provider': 'ollama',
            'name': model.text.trim(),
            'endpoint': endpoint.text.trim(),
          },
        if (choice == 'remote')
          'model': {
            'provider': preset,
            'name': model.text.trim(),
            if (endpoint.text.trim().isNotEmpty)
              'endpoint': endpoint.text.trim(),
            if (credential.text.trim().isNotEmpty)
              'credential_env': credential.text.trim(),
          },
      };

  bool get _valid => switch (choice) {
        'local' => model.text.trim().isNotEmpty && endpoint.text.trim().isNotEmpty,
        'remote' => model.text.trim().isNotEmpty,
        _ => true,
      };

  Future<void> _browseWorkspace() async {
    final selected = await widget.picker
        .pickDirectory(initial: workspace.text.isEmpty ? null : workspace.text);
    if (selected == null || selected == workspace.text) return;
    setState(() => workspace.text = selected);
  }

  Future<void> _save() async {
    if (!_valid || c.busy) return;
    if (choice == 'later') {
      widget.onClose?.call();
      return;
    }
    await c.reviewConfiguration(_selection, workspace.text.trim());
    if (c.reviewedAction == 'package-configure-review' && c.canConfirm) {
      await c.confirm();
    }
    if (c.notice.isNotEmpty) widget.onStart();
  }

  @override
  Widget build(BuildContext context) {
    final configured = c.modelConfigured;
    return SetupShell(
        title: 'Get started with Odessy',
        subtitle: configured
            ? 'Review your setup and start chatting.'
            : 'Odessy is installed. Choose how it should reach a model.',
        footer: SetupFooter(
            primaryLabel: configured ? 'Start chatting' : 'Save and continue',
            onPrimary: c.busy
                ? null
                : configured
                    ? widget.onStart
                    : (_valid ? _save : null),
            secondaryLabel: 'Configure later',
            onSecondary: c.busy ? null : widget.onClose,
            busy: c.busy,
            note: configured
                ? 'Start chatting is available because a model is configured and the local coordinator is ready.'
                : 'No model is downloaded and no provider is contacted until you ask.'),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (c.error.isNotEmpty)
            SetupMessage(
                error: true,
                text: c.error,
                actionLabel: 'Retry',
                onAction: c.busy ? null : c.refreshStatus),
          if (c.notice.isNotEmpty) SetupMessage(text: c.notice),
          if (choice == 'done')
            _configured(context, c)
          else ...[
            SetupSection(
                title: 'Where should the model run?',
                description:
                    'Choose an existing local model, a compatible remote endpoint, or finish later. Nothing is pulled automatically.',
                child: SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(
                          value: 'local',
                          icon: Icon(Icons.computer_outlined, size: 18),
                          label: Text('On this computer')),
                      ButtonSegment(
                          value: 'remote',
                          icon: Icon(Icons.cloud_outlined, size: 18),
                          label: Text('Remote or cloud')),
                      ButtonSegment(
                          value: 'later',
                          icon: Icon(Icons.schedule_outlined, size: 18),
                          label: Text('Configure later')),
                    ],
                    selected: {choice},
                    onSelectionChanged: c.busy
                        ? null
                        : (value) => setState(() => choice = value.first))),
            if (choice == 'local') _local(context),
            if (choice == 'remote') _remote(context),
            if (choice == 'later')
              const SetupMessage(
                  text:
                      'You can finish setup now. Odessy will start the first time a model is configured in Settings.'),
            SetupSection(
                title: 'Workspace',
                description:
                    'The folder Odessy may work in for file and terminal tools. It is never inside the installation.',
                child: PathField(
                    fieldKey: const Key('get-started-workspace'),
                    controller: workspace,
                    label: 'Workspace folder',
                    hint: 'Choose an existing folder',
                    enabled: !c.busy,
                    onBrowse: _browseWorkspace)),
            _checklist(context, c),
          ],
        ]));
  }

  Widget _local(BuildContext context) => SetupSection(
          title: 'On this computer',
          description:
              'Odessy reads the model list from the endpoint you name. It does not install or download anything.',
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(
                  child: PathField(
                      fieldKey: const Key('get-started-endpoint'),
                      controller: endpoint,
                      label: 'Ollama endpoint',
                      hint: 'http://127.0.0.1:11434',
                      enabled: !c.busy,
                      onChanged: (_) => setState(() {}))),
            ]),
            OutlinedButton.icon(
                key: const Key('get-started-discover'),
                onPressed: c.busy || c.discovering
                    ? null
                    : () => c.discover(endpoint: endpoint.text.trim()),
                icon: const Icon(Icons.search, size: 18),
                label: Text(c.discovering
                    ? 'Reading the endpoint…'
                    : 'Find installed models')),
            const SizedBox(height: OdessyTokens.spaceMedium),
            _models(context),
          ]));

  Widget _models(BuildContext context) {
    final list = c.discovery['models'];
    final names = <String>[
      if (list is List)
        for (final entry in list)
          if (entry is Map && entry['name'] is String) entry['name'] as String
    ];
    if (names.isNotEmpty) {
      return DropdownButtonFormField<String>(
          key: const Key('get-started-model-list'),
          initialValue: names.contains(model.text.trim()) ? model.text.trim() : null,
          decoration: const InputDecoration(labelText: 'Installed model'),
          items: [
            for (final name in names)
              DropdownMenuItem(value: name, child: Text(name))
          ],
          onChanged: c.busy
              ? null
              : (value) => setState(() => model.text = value ?? ''));
    }
    return TextField(
        key: const Key('get-started-model'),
        controller: model,
        enabled: !c.busy,
        onChanged: (_) => setState(() {}),
        decoration: InputDecoration(
            labelText: 'Model name',
            hintText: 'For example llama3.1:8b',
            helperText: c.discovery['status'] == 'empty'
                ? 'The endpoint answered but has no models. Odessy will not download one.'
                : c.discovery['status'] == 'unreachable'
                    ? 'Ollama is not answering at this address. Start it, or choose a remote endpoint.'
                    : 'Type the exact name of a model you already have.'));
  }

  Widget _remote(BuildContext context) => SetupSection(
          title: 'Remote or cloud',
          description:
              'Choose a supported provider preset or a compatible endpoint, then name the model. Authentication is referenced by environment-variable name only; no key is entered here.',
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            DropdownButtonFormField<String>(
                initialValue: preset,
                decoration: const InputDecoration(labelText: 'Provider'),
                items: const [
                  DropdownMenuItem(value: 'openai', child: Text('OpenAI-compatible')),
                  DropdownMenuItem(value: 'deepseek', child: Text('DeepSeek')),
                  DropdownMenuItem(value: 'qwen', child: Text('Qwen')),
                  DropdownMenuItem(value: 'custom', child: Text('Custom endpoint')),
                ],
                onChanged: c.busy
                    ? null
                    : (value) => setState(() => preset = value ?? 'openai')),
            const SizedBox(height: OdessyTokens.spaceMedium),
            TextField(
                key: const Key('get-started-remote-model'),
                controller: model,
                enabled: !c.busy,
                onChanged: (_) => setState(() {}),
                decoration: const InputDecoration(
                    labelText: 'Model name', hintText: 'For example gpt-4.1-mini')),
            const SizedBox(height: OdessyTokens.spaceMedium),
            TextField(
                key: const Key('get-started-remote-endpoint'),
                controller: endpoint,
                enabled: !c.busy,
                decoration: const InputDecoration(
                    labelText: 'Endpoint (optional for presets)',
                    hintText: 'https://…')),
            const SizedBox(height: OdessyTokens.spaceMedium),
            TextField(
                key: const Key('get-started-credential'),
                controller: credential,
                enabled: !c.busy,
                decoration: const InputDecoration(
                    labelText: 'Credential environment variable (optional)',
                    hintText: 'ODESSY_CLOUD_API_KEY',
                    helperText:
                        'The variable name only. Keys stay in the environment; they are never stored in settings.')),
          ]));

  Widget _configured(BuildContext context, InstalledManageController c) =>
      SetupSection(
          title: 'Ready',
          description: 'This installation is configured.',
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text(
                'The local coordinator starts when you continue. If it cannot start, setup stays open and shows the reason.'),
            const SizedBox(height: OdessyTokens.spaceMedium),
            OutlinedButton(
                onPressed: c.busy ? null : c.refreshStatus,
                child: const Text('Re-read installation state')),
          ]));

  Widget _checklist(BuildContext context, InstalledManageController c) {
    final checked = <(String, bool, String)>[
      ('Model configured', c.modelConfigured,
          c.modelConfigured ? 'Configured' : 'Not configured yet'),
      ('Endpoint reachable (when tested)', c.discovery['status'] == 'ok',
          c.discovery['status'] == 'ok' ? 'Answered' : 'Not tested'),
      ('Workspace selected', workspace.text.trim().isNotEmpty,
          workspace.text.trim().isEmpty ? 'Not selected' : workspace.text.trim()),
      ('Voice', false, 'Optional; not configured in this flow'),
    ];
    return SetupSection(
        title: 'Setup checklist',
        description:
            'This is configuration state, not a promise that a model reply will succeed.',
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          for (final item in checked)
            Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(children: [
                  Icon(
                      item.$2
                          ? Icons.check_circle
                          : Icons.radio_button_unchecked,
                      size: 16,
                      color: item.$2 ? OdessyTokens.accent : Colors.grey),
                  const SizedBox(width: OdessyTokens.spaceCompact),
                  Expanded(child: Text(item.$1)),
                  Flexible(
                      child: Tooltip(
                          message: item.$3,
                          child: Text(middleEllipsis(item.$3, 28),
                              maxLines: 1,
                              overflow: TextOverflow.clip,
                              style: Theme.of(context).textTheme.bodySmall))),
                ])),
        ]));
  }
}
