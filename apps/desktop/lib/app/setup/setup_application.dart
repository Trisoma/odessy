import 'dart:convert';
import 'dart:io';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../features/setup/controllers/installed_manage_controller.dart';
import '../../features/setup/controllers/package_setup_controller.dart';
import '../../features/setup/presentation/manage_page.dart';
import '../../features/setup/presentation/path_field.dart';
import '../../features/setup/presentation/setup_shell.dart';
import '../../features/setup/presentation/stage_list.dart';
import '../theme.dart';
import 'installed_startup.dart' show exitApp;
import 'launch_acknowledgement.dart';
import 'local_setup_backend.dart';
import 'native_picker.dart';
import 'platform_copy.dart';

String? installationMode(List<String> args) => args.contains('--installed')
    ? 'installed'
    : args.contains('--uninstall')
        ? 'uninstall'
        : args.contains('--setup')
            ? 'setup'
            : args.contains('--manage')
                ? 'manage'
                : null;

typedef SetupBackendFactory = SetupBackend Function();

/// Writes the bounded installation log to a user-chosen file. Kept here so
/// presentation never performs filesystem work.
Future<void> exportLines(String path, List<String> lines) async {
  final file = File(path);
  await file.parent.create(recursive: true);
  await file.writeAsString(lines.join('\n'), flush: true);
}

class SetupApplication extends StatefulWidget {
  const SetupApplication({
    super.key,
    required this.mode,
    this.payloadRoot = '',
    this.installRoot = '',
    this.backendFactory,
    this.picker = const UnavailablePicker(),
    this.acknowledgement,
    this.onExit,
  });

  final String mode, payloadRoot, installRoot;
  final SetupBackendFactory? backendFactory;
  final NativePicker picker;
  final LaunchAcknowledgement? acknowledgement;
  final Future<void> Function()? onExit;

  @override
  State<SetupApplication> createState() => _SetupApplicationState();
}

class _SetupApplicationState extends State<SetupApplication> {
  late final PackageSetupController controller;
  final location = TextEditingController();
  final model = TextEditingController();
  final endpoint = TextEditingController(text: 'http://127.0.0.1:11434');
  final credential = TextEditingController();
  final whisperBin = TextEditingController();
  final whisperModel = TextEditingController();
  final kokoroModel = TextEditingController();
  final kokoroVoices = TextEditingController();

  int step = 0;
  String role = 'combined', provider = 'later';
  bool voice = false, customSetup = false;
  String? locationError;
  String launchError = '';
  bool launching = false;
  InstalledManageController? management;
  late final AppLifecycleListener lifecycle;
  bool _closing = false;

  PackageSetupController get c => controller;

  /// The State's own context sits above the MaterialApp, so theme lookups here
  /// must use the same theme the surface is built with.
  ThemeData get _theme => odessyTheme(Brightness.dark);

  SetupBackend _newBackend() =>
      (widget.backendFactory ??
          () => LocalSetupBackend(payloadRoot: widget.payloadRoot))();

  @override
  void initState() {
    super.initState();
    controller =
        PackageSetupController(_newBackend(), progressBackend: _newBackend());
    controller.addListener(_refresh);
    lifecycle = AppLifecycleListener(onExitRequested: () async {
      if (c.busy) {
        final keep = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
                    title: const Text('Cancel and close setup?'),
                    content: const Text(
                        'Reopen setup to inspect interrupted work. Nothing is automatically repeated.'),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(ctx, true),
                          child: const Text('Keep open')),
                      TextButton(
                          onPressed: () => Navigator.pop(ctx, false),
                          child: const Text('Cancel and close')),
                    ]));
        if (keep != false) return AppExitResponse.cancel;
      }
      await _shutdown();
      return AppExitResponse.exit;
    });
    _load();
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  Future<void> _load() async {
    c.root = widget.installRoot;
    await c.run('context');
    if (!mounted) return;
    location.text = c.root;
    setState(() {});
    if (widget.mode == 'manage' || widget.mode == 'uninstall') {
      _openManagement(reviewUninstall: widget.mode == 'uninstall');
    }
  }

  Future<void> _shutdown() async {
    if (_closing) return;
    _closing = true;
    await c.close();
    management?.dispose();
  }

  Future<void> _closeApplication() async {
    await _shutdown();
    final exit = widget.onExit;
    if (exit != null) {
      await exit();
      return;
    }
    // Setup owns no coordinator; closing it ends this process. The downloaded
    // setup file is left alone.
    exitApp();
  }

  bool get _voiceComplete =>
      whisperBin.text.trim().isNotEmpty &&
      whisperModel.text.trim().isNotEmpty &&
      kokoroModel.text.trim().isNotEmpty &&
      kokoroVoices.text.trim().isNotEmpty;

  bool get _voiceInvalid => voice && !_voiceComplete;

  bool get _modelIncomplete =>
      role != 'execution' &&
      provider != 'later' &&
      (model.text.trim().isEmpty || endpoint.text.trim().isEmpty);

  Json _selection() => {
        'role': role,
        if (role != 'execution' && provider != 'later')
          'model': {
            'provider': provider,
            'name': model.text.trim(),
            if (endpoint.text.trim().isNotEmpty)
              'endpoint': endpoint.text.trim(),
            if (credential.text.trim().isNotEmpty)
              'credential_env': credential.text.trim(),
          },
        if (role != 'execution')
          'voice': {
            'enabled': voice && _voiceComplete,
            if (voice && _voiceComplete) ...{
              'whisper_bin': whisperBin.text.trim(),
              'whisper_model': whisperModel.text.trim(),
              'kokoro_model': kokoroModel.text.trim(),
              'kokoro_voices': kokoroVoices.text.trim(),
            },
          },
      };

  void _edit() => c.edit(destination: location.text, value: _selection());

  void _openManagement({bool reviewUninstall = false}) {
    final controller = InstalledManageController(c.backend, spec: {
      'root': c.root,
      // Repair and update recreate the active version from this payload. Without
      // it the action stays disabled even though the caller supplied one.
      if (widget.payloadRoot.isNotEmpty) 'payload': widget.payloadRoot,
    });
    setState(() => management = controller);
    controller.load().then((_) {
      if (reviewUninstall && !controller.uninstalled) {
        controller.reviewUninstall();
      }
    });
  }

  Future<void> _browseInstallFolder() async {
    final selected = await widget.picker.pickDirectory(
        initial: location.text.isEmpty ? null : location.text);
    if (selected == null || selected.trim().isEmpty) return;
    location.text = selected;
    setState(() => locationError = null);
    await _checkLocation();
  }

  Future<void> _browseVoice(TextEditingController target,
      {List<String> extensions = const []}) async {
    final selected = await widget.picker.pickFile(extensions: extensions);
    if (selected == null || selected.trim().isEmpty) return;
    target.text = selected;
    _edit();
    setState(() {});
  }

  Future<void> _checkLocation() async {
    if (c.busy) return;
    c.edit(destination: location.text);
    await c.run('context');
    if (!mounted) return;
    location.text = c.root;
    setState(() =>
        locationError = c.existingInstallation ? 'An installation already exists here.' : null);
  }

  @override
  void dispose() {
    lifecycle.dispose();
    controller.removeListener(_refresh);
    controller.dispose();
    management?.dispose();
    for (final editor in [
      location,
      model,
      endpoint,
      credential,
      whisperBin,
      whisperModel,
      kokoroModel,
      kokoroVoices,
    ]) {
      editor.dispose();
    }
    super.dispose();
  }

  // ---------------------------------------------------------------- steps

  Widget _welcome() => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SetupSection(
            title: 'Install Odessy on this computer',
            description:
                'Setup verifies the downloaded package, installs it into a folder you choose, $registrationTargetCopy',
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const _Bullet(
                      'Nothing is downloaded from the internet and no model is pulled.'),
                  const _Bullet(
                      'A model you already have can be selected next, or configured later.'),
                  _Bullet(accountScopeCopy),
                ])),
        const SetupMessage(
            text:
                'Installing files and getting ready to chat are separate. You can configure a model after installation.'),
      ]);

  Widget _machine() => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SetupSection(
            title: 'Where should Odessy be installed?',
            description:
                'Use a dedicated folder. Only files Odessy records are removed later; other files are preserved.',
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  PathField(
                      fieldKey: const Key('setup-location'),
                      controller: location,
                      label: 'Installation folder',
                      hint: r'C:\Users\you\AppData\Local\Odessy\App',
                      error: locationError,
                      enabled: !c.busy,
                      onChanged: (_) => _edit(),
                      onBrowse: _browseInstallFolder),
                  Align(
                      alignment: Alignment.centerLeft,
                      child: OutlinedButton.icon(
                          onPressed: c.busy ? null : _checkLocation,
                          icon: const Icon(Icons.check_circle_outline, size: 18),
                          label: const Text('Check this folder'))),
                  // Checking a folder can fail too, and a search field with no
                  // answer looks like it did nothing.
                  if (c.error.isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(
                            top: OdessyTokens.spaceMedium),
                        child: SetupMessage(
                            error: true, text: c.error, details: c.reason)),
                ])),
        if (c.existingInstallation)
          SetupMessage(
              text: 'Odessy is already installed in this folder.',
              details:
                  'Installing into the same folder is refused. Use management to repair, update, roll back, reconfigure or uninstall it.',
              actionLabel: 'Manage this installation',
              onAction: c.busy ? null : () => _openManagement()),
        SetupSection(
            title: 'This computer',
            description: customSetup
                ? 'Choose the role this machine performs.'
                : 'Standard setup installs a coordinator that also runs tools on this computer.',
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (!customSetup)
                    TextButton.icon(
                        onPressed: () => setState(() => customSetup = true),
                        icon: const Icon(Icons.tune, size: 18),
                        label: const Text('Custom setup')),
                  if (customSetup)
                    DropdownButtonFormField<String>(
                        initialValue: role,
                        decoration: const InputDecoration(labelText: 'Role'),
                        items: const [
                          DropdownMenuItem(
                              value: 'combined',
                              child: Text('Coordinator that also runs tools locally')),
                          DropdownMenuItem(
                              value: 'coordinator',
                              child: Text('Coordinator only (tools run elsewhere)')),
                          DropdownMenuItem(
                              value: 'execution',
                              child: Text('Execution node owned by another coordinator')),
                        ],
                        onChanged: c.busy
                            ? null
                            : (value) {
                                setState(() => role = value ?? 'combined');
                                _edit();
                              }),
                ])),
      ]);

  Widget _modelAndVoice() {
    final models = <String>[];
    final discoveryModels = c.discovery['models'];
    if (discoveryModels is List) {
      for (final entry in discoveryModels) {
        if (entry is Map && entry['name'] is String) {
          models.add(entry['name'] as String);
        }
      }
    }
    if (role == 'execution') {
      return const SetupSection(
          title: 'Execution node',
          description:
              'Execution nodes run tools for another coordinator. They need no model, voice or desktop runtime.',
          child: SizedBox.shrink());
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SetupSection(
          title: 'Model',
          description:
              'Optional now. Selecting an existing model here does not start it or download anything.',
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                DropdownButtonFormField<String>(
                    key: const Key('setup-model-provider'),
                    initialValue: provider,
                    decoration: const InputDecoration(labelText: 'Model source'),
                    items: const [
                      DropdownMenuItem(
                          value: 'later', child: Text('Configure a model later')),
                      DropdownMenuItem(
                          value: 'ollama',
                          child: Text('An Ollama model already installed')),
                      DropdownMenuItem(
                          value: 'openai',
                          child: Text('A compatible remote endpoint')),
                    ],
                    onChanged: c.busy
                        ? null
                        : (value) {
                            setState(() => provider = value ?? 'later');
                            _edit();
                          }),
                if (provider != 'later') ...[
                  const SizedBox(height: OdessyTokens.spaceMedium),
                  PathField(
                      fieldKey: const Key('setup-endpoint'),
                      controller: endpoint,
                      label: 'Endpoint',
                      hint: 'http://127.0.0.1:11434',
                      enabled: !c.busy,
                      onChanged: (_) => _edit()),
                  if (provider == 'ollama')
                    Align(
                        alignment: Alignment.centerLeft,
                        child: OutlinedButton.icon(
                            onPressed: c.busy || c.discovering
                                ? null
                                : () =>
                                    c.discover(endpoint: endpoint.text.trim()),
                            icon: const Icon(Icons.search, size: 18),
                            label: Text(c.discovering
                                ? 'Reading the endpoint…'
                                : 'Find installed models'))),
                  const SizedBox(height: OdessyTokens.spaceMedium),
                  if (models.isNotEmpty)
                    DropdownButtonFormField<String>(
                        initialValue: models.contains(model.text.trim())
                            ? model.text.trim()
                            : null,
                        decoration:
                            const InputDecoration(labelText: 'Installed model'),
                        items: [
                          for (final name in models)
                            DropdownMenuItem(value: name, child: Text(name))
                        ],
                        onChanged: c.busy
                            ? null
                            : (value) {
                                model.text = value ?? '';
                                _edit();
                              })
                  else
                    TextField(
                        key: const Key('setup-model'),
                        controller: model,
                        enabled: !c.busy,
                        onChanged: (_) => _edit(),
                        decoration: InputDecoration(
                            labelText: 'Existing model name',
                            hintText: 'For example llama3.1:8b',
                            helperText: c.discovery['status'] == 'empty'
                                ? 'The endpoint answered but has no models installed. Odessy will not download one.'
                                : c.discovery['status'] == 'unreachable'
                                    ? 'The endpoint did not answer. Check that it is running; nothing was installed.'
                                    : null)),
                  if (provider != 'ollama')
                    TextField(
                        controller: credential,
                        enabled: !c.busy,
                        onChanged: (_) => _edit(),
                        decoration: const InputDecoration(
                            labelText:
                                'Credential environment variable (optional)',
                            hintText: 'ODESSY_CLOUD_API_KEY',
                            helperText:
                                'The variable name only. Never enter a key here.')),
                ],
              ])),
      SetupSection(
          title: 'Voice',
          description:
              'Optional. Uses components you already have; nothing is downloaded. All four paths are required to enable it.',
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SwitchListTile(
                key: const Key('setup-voice-toggle'),
                contentPadding: EdgeInsets.zero,
                title: const Text('Use existing voice components'),
                value: voice,
                onChanged: c.busy
                    ? null
                    : (value) {
                        setState(() => voice = value);
                        _edit();
                      }),
            if (voice) ...[
              PathField(
                  fieldKey: const Key('setup-whisper-bin'),
                  controller: whisperBin,
                  label: 'Whisper.cpp executable',
                  enabled: !c.busy,
                  onChanged: (_) => _edit(),
                  onBrowse: () =>
                      _browseVoice(whisperBin, extensions: const ['exe'])),
              PathField(
                  fieldKey: const Key('setup-whisper-model'),
                  controller: whisperModel,
                  label: 'Whisper model file',
                  enabled: !c.busy,
                  onChanged: (_) => _edit(),
                  onBrowse: () =>
                      _browseVoice(whisperModel, extensions: const ['bin'])),
              PathField(
                  fieldKey: const Key('setup-kokoro-model'),
                  controller: kokoroModel,
                  label: 'Kokoro model file',
                  enabled: !c.busy,
                  onChanged: (_) => _edit(),
                  onBrowse: () =>
                      _browseVoice(kokoroModel, extensions: const ['onnx'])),
              PathField(
                  fieldKey: const Key('setup-kokoro-voices'),
                  controller: kokoroVoices,
                  label: 'Kokoro voices file',
                  enabled: !c.busy,
                  onChanged: (_) => _edit(),
                  onBrowse: () =>
                      _browseVoice(kokoroVoices, extensions: const ['bin'])),
              if (_voiceInvalid)
                const SetupMessage(
                    error: true,
                    text:
                        'Voice stays unavailable until all four asset paths are supplied, or turn it off to continue with text only.'),
            ],
          ])),
    ]);
  }

  Widget _review() {
    final review = c.review;
    final components = review['components'];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SetupSection(
          title: 'Review before installing',
          description:
              'This is exactly what will be written. Editing any option invalidates the review.',
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _row('Role', role),
            const SizedBox(height: 2),
            SummaryPath(path: c.root, label: 'Install into'),
            _row(
                'Model',
                provider == 'later'
                    ? 'Configure later — nothing is started'
                    : model.text.trim()),
            _row('Voice', voice && _voiceComplete ? 'Existing components' : 'Text only'),
            _row('Downloads', 'None. This is an offline package.'),
            _row('Service', 'Not created here; available later under Installation.'),
          ])),
      if (review.isEmpty) ...[
        FilledButton(
            onPressed: c.busy ? null : () => c.run('package-review'),
            child: Text(c.busy
                ? 'Reviewing…'
                : c.error.isEmpty
                    ? 'Review this installation'
                    : 'Review again')),
        // A review that fails writes nothing, so the only honest next step is
        // to say why. Without this the step looks stuck: the button still shows
        // and Install stays disabled because no plan was produced.
        if (c.error.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: OdessyTokens.spaceMedium),
              child: SetupMessage(error: true, text: c.error, details: c.reason)),
      ] else
        SetupSection(
            title: 'Components',
            description: 'Packaged components this installation will contain.',
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Wrap(spacing: 8, runSpacing: 8, children: [
                if (components is List)
                  for (final item in components.whereType<String>())
                    Chip(label: Text(item)),
              ]),
              if (review['absent_components'] is List &&
                  (review['absent_components'] as List).isNotEmpty)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: Text(
                        'Not included in this package: ${(review['absent_components'] as List).join(', ')}. Core tools still work.',
                        style: _theme.textTheme.bodySmall)),
              const SizedBox(height: OdessyTokens.spaceMedium),
              ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: const Text('Technical details'),
                  children: [
                    SizedBox(
                        height: 220,
                        child: SingleChildScrollView(
                            child: SelectionArea(
                                child: Text(
                                    const JsonEncoder.withIndent('  ')
                                        .convert(review),
                                    style: const TextStyle(fontSize: 11)))))
                  ]),
            ])),
    ]);
  }

  Widget _row(String label, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
            width: 108,
            child: Text(label, style: _theme.textTheme.bodySmall)),
        Expanded(child: Text(value)),
      ]));

  Widget _progress() => SetupSection(
      title: c.interrupted && !c.busy ? 'Installation stopped' : 'Installing Odessy',
      description: c.busy
          ? 'Preparation and package checks finished before this page opened; their recorded times are retained. The remaining stages follow the installation journal.'
          : c.interrupted
              ? 'A previous version, if any, stays active and no files were removed.'
              : 'Installation finished.',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (c.busy) ...[
          LinearProgressIndicator(value: c.progress),
          const SizedBox(height: OdessyTokens.spaceCompact),
          Text(
              c.longRunning
                  ? 'Still working · ${formatElapsed(c.elapsed)} elapsed · ${c.stage}'
                  : formatElapsed(c.elapsed),
              style: _theme.textTheme.bodySmall),
          const SizedBox(height: OdessyTokens.spaceMedium),
        ],
        if (c.stepsTotal > 0)
          Text('${c.stepsDone} of ${c.stepsTotal} packaged steps completed',
              style: _theme.textTheme.bodySmall),
        const SizedBox(height: OdessyTokens.spaceCompact),
        StageList(stages: c.visibleStages),
        if (c.error.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: OdessyTokens.spaceMedium),
              child: SetupMessage(error: true, text: c.error, details: c.reason)),
        if (c.interrupted && !c.busy)
          Align(
              alignment: Alignment.centerLeft,
              child: FilledButton.tonal(
                  onPressed: () {
                    setState(() => step = 3);
                    c.run('package-review');
                  },
                  child: const Text('Inspect and review recovery'))),
        InstallOutputPane(
            lines: c.output,
            running: c.busy,
            onExport: c.output.isEmpty ? null : _exportOutput),
      ]));

  Future<void> _exportOutput() async {
    final target = await widget.picker.pickSaveLocation(
        suggestedName: 'odessy-installation-output.txt',
        extensions: const ['txt']);
    if (target == null) return;
    await exportLines(target, c.output);
    if (!mounted) return;
    setState(() => launchError = 'Installation output saved.');
  }

  Widget _finish() {
    final result = c.result;
    final registration = (result['registration'] as String?) ?? 'skipped';
    final configured = role != 'execution' && provider != 'later';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SetupSection(
          title: 'Odessy is installed',
          description:
              openFromMenuCopy,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SummaryPath(path: c.root, label: 'Installed at'),
            _row(
                'State',
                configured
                    ? 'Installed and configured — not yet verified'
                    : 'Installed — model not configured'),
            _row(
                registrationRowLabel,
                registration == 'complete' || registration == 'skipped'
                    ? 'Created'
                    : 'Needs repair — use Manage installation'),
            if (result['registration_error'] != null)
              Padding(
                  padding:
                      const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                  child: Text('${result['registration_error']}',
                      style: _theme.textTheme.bodySmall)),
          ])),
      if (launchError.isNotEmpty)
        SetupMessage(
            text: launchError,
            error: true,
            actionLabel: launching ? null : 'Open Odessy again',
            onAction: _openAndFinish),
      ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('Installation details'),
          children: [
            SelectableText(const JsonEncoder.withIndent('  ').convert(result),
                style: const TextStyle(fontSize: 11))
          ]),
    ]);
  }

  Future<void> _openAndFinish() async {
    if (launching) return;
    final installationId = c.context['installation_id'];
    if (installationId is! String || installationId.isEmpty) {
      setState(() => launchError =
          'The installation identity is unavailable, so setup cannot confirm a hand-off.');
      return;
    }
    setState(() {
      launching = true;
      launchError = '';
    });
    // The identifier is ours, not the helper's, and it is passed through the
    // launch so the receipt can only answer for this attempt.
    final launchId = LaunchAcknowledgement.newId();
    final acknowledgement = widget.acknowledgement ??
        BackendLaunchAcknowledgement(c.progressBackend, root: c.root);
    try {
      final launched =
          await c.call('package-launch', parameters: {'launch_id': launchId});
      if (!mounted) return;
      if (launched['launched'] != true) {
        setState(() => launchError =
            'Odessy did not start. Setup will stay open so you can retry or close.');
        return;
      }
      final state = await acknowledgement.waitFor(launchId);
      if (!mounted) return;
      if (state == launchReady || state == launchConfigurationRequired) {
        await _closeApplication();
        return;
      }
      setState(() => launchError = state == launchFailed
          ? 'Odessy started but reported that it could not finish starting. Setup stays open; use Retry or Close.'
          : 'Odessy was started but has not reported readiness yet. Setup stays open; use Retry or Close.');
    } catch (failure) {
      if (!mounted) return;
      setState(() => launchError =
          'Odessy could not be started: ${failure.toString().replaceFirst('Bad state: ', '')}');
    } finally {
      if (mounted) setState(() => launching = false);
    }
  }

  // ---------------------------------------------------------------- shell

  static const _titles = [
    'Welcome',
    'Installation folder',
    'Model and voice',
    'Review',
    'Install',
    'Finish',
  ];
  static const _subtitles = [
    'A local assistant owned by you.',
    'Choose where Odessy lives on this computer.',
    'Optional now; change it any time in settings.',
    'Exactly what will be written.',
    'Named stages, real progress.',
    'Open Odessy or close setup.',
  ];

  bool get _canContinue => switch (step) {
        0 => true,
        1 => c.root.trim().isNotEmpty && !c.existingInstallation,
        2 => !_modelIncomplete && !_voiceInvalid,
        3 => c.review.isNotEmpty,
        _ => true,
      };

  Future<void> _primary() async {
    if (step == 3) {
      setState(() => step = 4);
      await c.run('package-install');
      if (!mounted) return;
      if (c.installed) {
        await c.run('context');
        if (!mounted) return;
        setState(() => step = 5);
      }
    } else if (step == 5) {
      await _openAndFinish();
    } else {
      setState(() => step++);
    }
  }

  Widget _body() {
    if (c.context.isEmpty) {
      return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const LinearProgressIndicator(),
        const SizedBox(height: OdessyTokens.spaceMedium),
        Text(c.error.isEmpty
            ? 'Preparing setup. This usually takes a few seconds.'
            : 'The installer package could not be verified. ${c.reason}'),
      ]);
    }
    return switch (step) {
      0 => _welcome(),
      1 => _machine(),
      2 => _modelAndVoice(),
      3 => _review(),
      4 => _progress(),
      _ => _finish(),
    };
  }

  Widget _managementSurface(InstalledManageController manage) => Column(
      children: [
        Expanded(
            child: ManagePage(
                controller: manage,
                external: widget.payloadRoot.isNotEmpty,
                onOpenMaintenance: null)),
        Padding(
            padding: const EdgeInsets.all(OdessyTokens.spaceRegular),
            child: TextButton(
                onPressed: widget.mode == 'manage' || widget.mode == 'uninstall'
                    ? _closeApplication
                    : () => setState(() => management = null),
                child: Text(
                    widget.mode == 'manage' || widget.mode == 'uninstall'
                        ? 'Close'
                        : 'Back to setup'))),
      ]);

  @override
  Widget build(BuildContext context) {
    final manage = management;
    final managing = manage != null ||
        widget.mode == 'manage' ||
        widget.mode == 'uninstall';
    final title = widget.mode == 'uninstall'
        ? 'Uninstall Odessy'
        : managing
            ? 'Manage Odessy'
            : 'Odessy Setup';
    return MaterialApp(
        title: title,
        debugShowCheckedModeBanner: false,
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            body: SafeArea(
                child: managing
                    ? Column(children: [
                        Padding(
                            padding: const EdgeInsets.symmetric(
                                horizontal: OdessyTokens.spaceLarge,
                                vertical: OdessyTokens.spaceRegular),
                            child: Row(children: [
                              const OdessyBrandMark(),
                              const SizedBox(width: OdessyTokens.spaceMedium),
                              Text(title,
                                  style:
                                      _theme.textTheme.titleLarge),
                            ])),
                        const Divider(height: 1),
                        // The management page reports its own failures; without
                        // this the setup step that produced its root stayed silent.
                        if (c.error.isNotEmpty)
                          Padding(
                              padding: const EdgeInsets.fromLTRB(
                                  OdessyTokens.spaceLarge,
                                  OdessyTokens.spaceMedium,
                                  OdessyTokens.spaceLarge,
                                  0),
                              child: SetupMessage(
                                  error: true,
                                  text: c.error,
                                  details: c.reason)),
                        Expanded(
                            child: manage == null
                                ? const Center(
                                    child: CircularProgressIndicator())
                                : _managementSurface(manage)),
                      ])
                    : SetupShell(
                        title: _titles[step],
                        subtitle: _subtitles[step],
                        stepLabel: 'Step ${step + 1} of ${_titles.length}',
                        footer: step < 4
                            ? SetupFooter(
                                primaryLabel:
                                    step == 3 ? 'Install' : 'Continue',
                                onPrimary:
                                    c.busy || !_canContinue ? null : _primary,
                                secondaryLabel: step == 0 ? null : 'Back',
                                onSecondary: step == 0
                                    ? null
                                    : () => setState(() => step--),
                                busy: c.busy)
                            : step == 4
                                ? SetupFooter(
                                    primaryLabel: 'Continue',
                                    onPrimary: c.installed ? _primary : null,
                                    secondaryLabel:
                                        c.busy ? 'Cancel installation' : 'Back',
                                    onSecondary: c.busy
                                        ? c.cancel
                                        : () => setState(() => step = 3),
                                    busy: false,
                                    note: c.busy
                                        ? 'Cancelling stops future steps; completed work is kept.'
                                        : null)
                                : SetupFooter(
                                    primaryLabel: launching
                                        ? 'Opening Odessy…'
                                        : 'Open Odessy and finish',
                                    onPrimary: _openAndFinish,
                                    secondaryLabel: 'Close',
                                    onSecondary: _closeApplication,
                                    busy: launching,
                                    note:
                                        'Setup closes only after Odessy reports that it is ready. The downloaded setup file is kept.'),
                        child: _body()))));
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.only(bottom: OdessyTokens.spaceCompact),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Icon(Icons.check_circle_outline, size: 16),
        const SizedBox(width: OdessyTokens.spaceCompact),
        Expanded(child: Text(text)),
      ]));
}
