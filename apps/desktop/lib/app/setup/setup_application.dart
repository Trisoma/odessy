import 'dart:async';
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
  bool voice = false, customSetup = false, voiceDownloadRequested = false;
  bool voiceDownloaded = false;
  String? locationError;
  String launchError = '';
  bool launching = false;
  InstalledManageController? management;
  late final AppLifecycleListener lifecycle;
  bool _closing = false;

  // Review runs by itself when the review step opens and again whenever an
  // option changes, so the user never has to press a separate button.
  bool reviewing = false;
  bool _reviewQueued = false;

  // Opt-in voice download. Absent plan means it was never asked for.
  Json voicePlan = const {};
  Json voiceState = const {};
  Json voiceDependencies = const {};
  bool voicePlanLoading = false, installingVoice = false;
  bool voiceDependenciesRunning = false;
  String voiceError = '', voiceNotice = '', voiceDependenciesError = '';
  Timer? _voicePoll;
  bool _voicePolling = false;
  double? voiceProgress;

  PackageSetupController get c => controller;

  /// The State's own context sits above the MaterialApp, so theme lookups here
  /// must use the same theme the surface is built with.
  ThemeData get _theme => odessyTheme(Brightness.dark);

  SetupBackend _newBackend() => (widget.backendFactory ??
      () => LocalSetupBackend(payloadRoot: widget.payloadRoot))();

  @override
  void initState() {
    super.initState();
    controller =
        PackageSetupController(_newBackend(), progressBackend: _newBackend());
    controller.addListener(_refresh);
    lifecycle = AppLifecycleListener(onExitRequested: () async {
      if (c.busy || installingVoice || voiceDependenciesRunning) {
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
    if (widget.mode == 'setup') {
      unawaited(_refreshVoiceState());
    }
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

  void _edit() {
    c.edit(destination: location.text, value: _selection());
    // Editing invalidates the reviewed plan. While the review step is showing,
    // it is regenerated immediately instead of leaving a stale page behind.
    if (step == 3) unawaited(_reviewNow());
  }

  Future<void> _reviewNow() async {
    if (c.busy || reviewing) {
      _reviewQueued = true;
      return;
    }
    setState(() => reviewing = true);
    try {
      await c.run('package-review');
    } finally {
      if (mounted) {
        setState(() => reviewing = false);
        if (_reviewQueued) {
          _reviewQueued = false;
          if (step == 3 && c.review.isEmpty) unawaited(_reviewNow());
        }
      }
    }
  }

  Future<void> _loadVoicePlan() async {
    if (voicePlanLoading || c.busy) return;
    setState(() => voicePlanLoading = true);
    try {
      final value = await c.call('voice-plan');
      if (!mounted) return;
      setState(() {
        voicePlan = value;
        voiceError = '';
      });
    } catch (failure) {
      if (!mounted) return;
      setState(() => voiceError =
          'Voice downloads could not be checked: ${failure.toString().replaceFirst('Bad state: ', '')}');
    } finally {
      if (mounted) setState(() => voicePlanLoading = false);
    }
  }

  Future<void> _refreshVoiceState() async {
    try {
      // The installation root lets the helper check the interpreter that will
      // actually import the voice modules, rather than guessing from files.
      final value = await c.call('voice-status',
          parameters: {if (c.root.trim().isNotEmpty) 'install_root': c.root});
      if (!mounted) return;
      setState(() => voiceState = value);
    } catch (_) {
      // Observational only; a missing record must not look like a failure.
    }
  }

  /// Install the downloaded Python voice packages into the installed runtime.
  ///
  /// This is the only step that needs an installation, so it runs after the
  /// installation completes and reports its own failure instead of blocking it.
  Future<void> _installVoiceDependencies() async {
    if (voiceDependenciesRunning || c.root.trim().isEmpty) return;
    setState(() {
      voiceDependenciesRunning = true;
      voiceDependenciesError = '';
    });
    try {
      final result = await c
          .call('voice-python-install', parameters: {'install_root': c.root});
      if (!mounted) return;
      setState(() {
        voiceDependencies = result;
        voiceDependenciesError = result['verified'] == true
            ? ''
            : '${result['manual_step'] ?? 'Voice dependencies are not ready.'}';
      });
      await _refreshVoiceState();
    } catch (failure) {
      if (!mounted) return;
      setState(() => voiceDependenciesError =
          'Voice dependencies could not be installed: ${failure.toString().replaceFirst('Bad state: ', '')} '
              'You can retry, or keep using text only.');
    } finally {
      if (mounted) setState(() => voiceDependenciesRunning = false);
    }
  }

  void _startVoicePolling() {
    _voicePoll?.cancel();
    _voicePoll = Timer.periodic(const Duration(milliseconds: 600), (_) async {
      if (!installingVoice || _voicePolling) return;
      _voicePolling = true;
      try {
        final reply = await c.progressBackend.execute(SetupRequest(
            id: 'voice-progress-${DateTime.now().microsecondsSinceEpoch}',
            operation: 'manage',
            selection: const {},
            parameters: {
              'action': 'voice-status',
              if (c.context['payload_root'] is String)
                'payload': c.context['payload_root'],
            }));
        if (!reply.ok) return;
        final value = reply.result;
        if (!mounted) return;
        final progress = value['progress'];
        final done =
            progress is Map ? (progress['downloaded_bytes'] as num?) : null;
        final total =
            progress is Map ? (progress['total_bytes'] as num?) : null;
        setState(() {
          voiceState = value;
          voiceProgress = done == null || total == null || total <= 0
              ? null
              : (done / total).clamp(0.0, 1.0).toDouble();
        });
      } catch (_) {
        // Progress is advisory; installation reports any actual failure.
      } finally {
        _voicePolling = false;
      }
    });
  }

  Future<bool> _installVoice() async {
    final planId = voicePlan['plan_id'];
    if (installingVoice || c.busy || planId is! String || planId.isEmpty) {
      return false;
    }
    setState(() {
      installingVoice = true;
      voiceError = '';
      voiceProgress = 0;
    });
    _startVoicePolling();
    try {
      final result = await c.call('voice-install', confirm: planId);
      if (!mounted) return false;
      final missing = result['missing'];
      final verified = result['verified'] == true;
      final ready = verified && (missing is! List || missing.isEmpty);
      if (!ready) {
        setState(() => voiceError =
            'Voice components could not be verified. ${result['manual_step'] ?? 'Try again.'}');
        return false;
      }
      final paths = result['paths'];
      if (paths is Map &&
          ['whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices']
              .every((key) =>
                  paths[key] is String && (paths[key] as String).isNotEmpty)) {
        for (final entry in <String, TextEditingController>{
          'whisper_bin': whisperBin,
          'whisper_model': whisperModel,
          'kokoro_model': kokoroModel,
          'kokoro_voices': kokoroVoices,
        }.entries) {
          final value = paths[entry.key];
          if (value is String && value.isNotEmpty) entry.value.text = value;
        }
        setState(() => voice = true);
        _edit();
      } else {
        setState(() => voiceError =
            'Voice components are incomplete. Try installation again.');
        return false;
      }
      final wheels = result['wheel_count'];
      setState(() {
        if (ready &&
            result['installed'] is List &&
            (result['installed'] as List).isNotEmpty) {
          voiceDownloaded = true;
        }
        voiceError = ready
            ? ''
            : 'Voice still needs attention: ${missing is List ? missing.join(', ') : 'some components are missing'}. '
                '${result['manual_step'] is String ? result['manual_step'] : ''}';
        // A success is not an error: the packages still have to be installed into
        // the runtime, so this is stated as a notice, not a failure.
        voiceNotice = ready && wheels is int && wheels > 0
            ? 'Voice components are downloaded. $wheels Python voice packages will be installed into Odessy after it is installed.'
            : (ready ? 'Voice components are ready.' : '');
      });
      await _refreshVoiceState();
      return _voiceComplete;
    } catch (failure) {
      if (!mounted) return false;
      // The remedy is part of the message: a bare failure leaves the user with a
      // Retry button and no idea what else to try.
      setState(() => voiceError =
          'Voice installation did not finish: ${failure.toString().replaceFirst('Bad state: ', '')} '
              'You can try again, select existing voice files above, or continue with text only.');
      return false;
    } finally {
      _voicePoll?.cancel();
      _voicePoll = null;
      if (mounted) setState(() => installingVoice = false);
    }
  }

  /// The explicit escape from a failed voice download. The path fields are left
  /// untouched, so nothing typed or detected is lost if the user changes course.
  void _continueWithoutVoice() {
    setState(() {
      voice = false;
      voiceDownloadRequested = false;
      voiceError = '';
      voiceNotice = '';
    });
    _edit();
  }

  void _openManagement({bool reviewUninstall = false}) {
    final controller = InstalledManageController(c.backend,
        spec: {'root': c.root},
        setupPayload: widget.mode == 'setup' ? widget.payloadRoot : null);
    setState(() => management = controller);
    controller.load().then((_) {
      if (reviewUninstall && !controller.uninstalled) {
        controller.reviewUninstall();
      }
    });
  }

  Future<void> _browseInstallFolder() async {
    final selected = await widget.picker
        .pickDirectory(initial: location.text.isEmpty ? null : location.text);
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
    setState(() => locationError =
        c.existingInstallation ? 'An installation already exists here.' : null);
  }

  @override
  void dispose() {
    _voicePoll?.cancel();
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

  Widget _welcome() =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SetupSection(
            title: 'Install Odessy on this computer',
            description:
                'Setup verifies the downloaded package, installs it into a folder you choose, $registrationTargetCopy',
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
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

  Widget _machine() =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SetupSection(
            title: 'Where should Odessy be installed?',
            description:
                'Use a dedicated folder. Only files Odessy records are removed later; other files are preserved.',
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
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
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceMedium),
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
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (!customSetup)
                TextButton.icon(
                    onPressed: () => setState(() => customSetup = true),
                    icon: const Icon(Icons.tune, size: 18),
                    label: const Text('Custom setup')),
              if (customSetup)
                DropdownButtonFormField<String>(
                    initialValue: role,
                    // Unconstrained dropdowns size themselves to their widest
                    // item, which overflows at large text on a narrow window.
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Role'),
                    items: const [
                      DropdownMenuItem(
                          value: 'combined',
                          child:
                              Text('Coordinator that also runs tools locally')),
                      DropdownMenuItem(
                          value: 'coordinator',
                          child:
                              Text('Coordinator only (tools run elsewhere)')),
                      DropdownMenuItem(
                          value: 'execution',
                          child: Text(
                              'Execution node owned by another coordinator')),
                    ],
                    onChanged: c.busy
                        ? null
                        : (value) {
                            setState(() {
                              role = value ?? 'combined';
                              if (role == 'execution') {
                                voiceDownloadRequested = false;
                              }
                            });
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
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            DropdownButtonFormField<String>(
                key: const Key('setup-model-provider'),
                initialValue: provider,
                isExpanded: true,
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
                            : () => c.discover(endpoint: endpoint.text.trim()),
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
                    isExpanded: true,
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
                        labelText: 'Credential environment variable (optional)',
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
      SetupSection(
          title: 'Voice downloads (optional)',
          description:
              'Whisper.cpp and Kokoro can be downloaded and verified for you. This needs an internet connection, and each file is checked before it is used.',
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SwitchListTile(
                key: const Key('setup-voice-download'),
                contentPadding: EdgeInsets.zero,
                title: const Text('Install voice components'),
                subtitle: Text(voicePlan['total_bytes'] is int
                    ? 'About ${_megabytes(voicePlan['total_bytes'] as int)} will be downloaded.'
                    : 'Choose this to see the download size first.'),
                value: voiceDownloadRequested,
                onChanged: c.busy || installingVoice
                    ? null
                    : (value) {
                        setState(() => voiceDownloadRequested = value);
                        if (value) unawaited(_loadVoicePlan());
                      }),
            if (voiceDownloadRequested) ...[
              if (voicePlanLoading)
                const Padding(
                    padding: EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: LinearProgressIndicator()),
              if (voicePlan['requires_internet'] == true)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: Text(
                        'An internet connection is required. Download starts when you install Odessy.',
                        style: _theme.textTheme.bodySmall)),
              // Say what the download costs the rest of the machine before it
              // happens, not after an unrelated write fails.
              if (voicePlan['space_warning'] is String &&
                  (voicePlan['space_warning'] as String).isNotEmpty)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: Text('Free space: ${voicePlan['space_warning']}',
                        style: _theme.textTheme.bodySmall
                            ?.copyWith(color: _theme.colorScheme.error))),
              if (voicePlan['available'] is List &&
                  (voicePlan['available'] as List).isNotEmpty) ...[
                const SizedBox(height: OdessyTokens.spaceCompact),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final item in (voicePlan['available'] as List)
                      .whereType<Map>()
                      .where((item) => item['kind'] != 'wheel'))
                    Chip(
                        label: Text(
                            '${item['label']} · ${_megabytes((item['size_bytes'] as num?)?.toInt() ?? 0)}')),
                  if ((voicePlan['available'] as List)
                      .whereType<Map>()
                      .any((item) => item['kind'] == 'wheel'))
                    Chip(
                        label: Text(
                            '${(voicePlan['available'] as List).whereType<Map>().where((item) => item['kind'] == 'wheel').length} '
                            'Python packages · ${_megabytes((voicePlan['available'] as List).whereType<Map>().where((item) => item['kind'] == 'wheel').fold<int>(0, (sum, item) => sum + ((item['size_bytes'] as num?)?.toInt() ?? 0)))}')),
                ]),
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: Text(
                        'Models and the speech engine are yours; the Python packages are installed into the Odessy installation so they cannot affect other software.',
                        style: _theme.textTheme.bodySmall)),
                const SizedBox(height: OdessyTokens.spaceCompact),
                // Nothing to fetch means nothing to ask: an eligible plan with
                // zero pending bytes is already installed.
                if (voicePlan['nothing_to_do'] == true)
                  Text(
                      'All voice components are already installed in ${voicePlan['root'] ?? 'the voice folder'}.',
                      style: _theme.textTheme.bodySmall)
              ],
              if (voicePlan['unavailable'] is List &&
                  (voicePlan['unavailable'] as List).isNotEmpty)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: Text(
                        'Not offered in this build: ${(voicePlan['unavailable'] as List).whereType<Map>().map((item) => '${item['label']} — ${item['reason']}').join('; ')}. Select existing files above instead.',
                        style: _theme.textTheme.bodySmall)),
              if (installingVoice) ...[
                const SizedBox(height: OdessyTokens.spaceCompact),
                LinearProgressIndicator(value: voiceProgress),
                const SizedBox(height: OdessyTokens.spaceCompact),
                Text(_voiceProgressCopy(voiceState['progress'])),
              ],
              if (voiceNotice.isNotEmpty)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child:
                        Text(voiceNotice, style: _theme.textTheme.bodySmall)),
              if (voiceError.isNotEmpty)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: SetupMessage(
                        error: true,
                        text: voiceError,
                        actionLabel: voicePlan.isEmpty ? 'Try again' : null,
                        onAction: voicePlan.isEmpty ? _loadVoicePlan : null)),
              // The alternative to retrying is always available: voice is
              // optional, so a failure must never strand the person here.
              if (voiceError.isNotEmpty && !installingVoice)
                Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton(
                        onPressed: _continueWithoutVoice,
                        child: const Text('Continue with text only'))),
              // One statement of state at a time: the notice already says the
              // components are downloaded, so repeating it here adds noise.
              if (voiceNotice.isEmpty &&
                  !voiceDownloadRequested &&
                  voiceState['components'] is List &&
                  (voiceState['components'] as List).isNotEmpty)
                Padding(
                    padding:
                        const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                    child: Text(_voiceReadinessCopy(voiceState))),
            ],
          ])),
    ]);
  }

  static String _megabytes(int bytes) => bytes <= 0
      ? 'unknown size'
      : '${(bytes / (1024 * 1024)).toStringAsFixed(0)} MB';

  String _voiceProgressCopy(Object? progress) {
    if (progress is! Map) return 'Downloading voice components…';
    final message = progress['message'] is String
        ? progress['message'] as String
        : 'Downloading voice components…';
    final done = (progress['downloaded_bytes'] as num?)?.toInt() ?? 0;
    final total = (progress['total_bytes'] as num?)?.toInt() ?? 0;
    return total > 0
        ? '$message · ${_megabytes(done)} of ${_megabytes(total)}'
        : message;
  }

  /// Readiness is only claimed when every required component was found and
  /// verified. Each finding carries its own remedy, so the sentence names the
  /// next action instead of only the symptom.
  String _voiceReadinessCopy(Json state) {
    final missing = state['missing'];
    final componentsReady = missing is! List || missing.isEmpty;
    final pythonReady =
        state['python'] is Map && (state['python'] as Map)['ready'] == true;
    if (state['ready'] == true && componentsReady) {
      return 'Voice is ready. Microphone and audio devices are checked separately in Settings.';
    }
    // Components on disk are not the same as an importable runtime; say which
    // half is missing instead of a generic "needs attention".
    if (componentsReady && !pythonReady) {
      return 'Voice components are downloaded. Their Python packages are installed into Odessy after it is installed.';
    }
    final findings = state['findings'];
    if (findings is List && findings.isNotEmpty) {
      final attention = findings
          .whereType<Map>()
          .where((item) => item['severity'] != 'info')
          .map((item) =>
              '${item['message'] ?? ''} ${item['fix_hint'] ?? ''}'.trim())
          .where((item) => item.isNotEmpty)
          .toList();
      if (attention.isNotEmpty) {
        return 'Voice needs attention: ${attention.join(' ')} '
            'Microphone and audio devices are checked separately.';
      }
    }
    final names = missing is List ? missing.join(', ') : 'some components';
    return 'Voice still needs attention: $names. Microphone and audio devices are checked separately.';
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
            _row(
                'Voice',
                voiceDownloadRequested
                    ? 'Voice components will be installed with Odessy'
                    : voice && voiceDownloaded && _voiceComplete
                        ? 'Installed voice components'
                        : voice && _voiceComplete
                            ? 'Existing components'
                            : 'Text only'),
            _row(
                'Downloads',
                voiceDownloadRequested
                    ? 'Voice components will be downloaded (${_megabytes((voicePlan['total_bytes'] as num?)?.toInt() ?? 0)}).'
                    : voiceDownloaded
                        ? 'Voice components were downloaded and verified before this review.'
                        : 'None. This is an offline package.'),
            _row('Service',
                'Not created here; available later under Installation.'),
          ])),
      if (voiceError.isNotEmpty) SetupMessage(error: true, text: voiceError),
      if (review.isEmpty) ...[
        if (reviewing || c.busy)
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const LinearProgressIndicator(),
            const SizedBox(height: OdessyTokens.spaceCompact),
            Text('Reviewing this installation…',
                style: _theme.textTheme.bodySmall),
          ])
        else if (c.error.isNotEmpty)
          SetupMessage(
              error: true,
              text: c.error,
              details: c.reason,
              actionLabel: 'Review again',
              onAction: _reviewNow)
        else
          Align(
              alignment: Alignment.centerLeft,
              child: FilledButton(
                  onPressed: _reviewNow,
                  child: const Text('Review this installation'))),
      ] else ...[
        Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
                onPressed: c.busy || reviewing ? null : _reviewNow,
                icon: const Icon(Icons.refresh, size: 18),
                label: Text(
                    reviewing ? 'Recalculating…' : 'Recalculate this review'))),
        if (reviewing) const LinearProgressIndicator(),
        if (c.error.isNotEmpty)
          SetupMessage(
              error: true,
              text: c.error,
              details: c.reason,
              actionLabel: 'Review again',
              onAction: _reviewNow),
      ],
      if (review.isNotEmpty)
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
            width: 108, child: Text(label, style: _theme.textTheme.bodySmall)),
        Expanded(child: Text(value)),
      ]));

  Widget _progress() => SetupSection(
      title: c.interrupted && !c.busy
          ? 'Installation stopped'
          : 'Installing Odessy',
      description: installingVoice
          ? 'Downloading and verifying the selected voice components.'
          : voiceDependenciesRunning
              ? 'Installing voice dependencies in the Odessy runtime.'
              : c.busy
                  ? 'Preparation and package checks finished before this page opened; their recorded times are retained. The remaining stages follow the installation journal.'
                  : c.interrupted
                      ? 'A previous version, if any, stays active and no files were removed.'
                      : 'Installation finished.',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (installingVoice || voiceDependenciesRunning) ...[
          LinearProgressIndicator(
              value: installingVoice ? voiceProgress : null),
          const SizedBox(height: OdessyTokens.spaceCompact),
          Text(installingVoice
              ? _voiceProgressCopy(voiceState['progress'])
              : 'Installing voice dependencies…'),
        ],
        if (voiceError.isNotEmpty) SetupMessage(error: true, text: voiceError),
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
              child:
                  SetupMessage(error: true, text: c.error, details: c.reason)),
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
          description: openFromMenuCopy,
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
      if (voice && _voiceComplete) _voiceDependenciesSection(),
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

  /// The Python side of voice lives in the installed runtime, so it is reported
  /// here, after the installation exists. Saying "ready" here without a passing
  /// import check would be a claim the machine cannot back up.
  Widget _voiceDependenciesSection() {
    final verified = voiceDependencies['verified'] == true;
    final severity = voiceState['severity'];
    return SetupSection(
        title: 'Voice',
        description:
            'The speech packages are installed into this Odessy installation, not system-wide.',
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (voiceDependenciesRunning) ...[
            const LinearProgressIndicator(),
            const SizedBox(height: OdessyTokens.spaceCompact),
            Text('Installing Python voice dependencies…',
                style: _theme.textTheme.bodySmall),
          ] else if (verified && (severity == 'info' || severity == null))
            const Text(
                'Voice components and their Python dependencies are installed and importable. '
                'Microphone and audio devices are checked separately in Settings.')
          else ...[
            Text(severity == 'error'
                ? 'Voice needs attention: a component or dependency did not check out.'
                : 'Voice dependencies are not installed yet.'),
            if (voiceDependenciesError.isNotEmpty)
              Padding(
                  padding:
                      const EdgeInsets.only(top: OdessyTokens.spaceCompact),
                  child: Text(voiceDependenciesError,
                      style: _theme.textTheme.bodySmall)),
            Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton.icon(
                    onPressed: voiceDependenciesRunning
                        ? null
                        : _installVoiceDependencies,
                    icon: const Icon(Icons.download_outlined, size: 18),
                    label: Text(verified
                        ? 'Check voice again'
                        : 'Install voice dependencies'))),
            Text('Odessy runs without voice; text works either way.',
                style: _theme.textTheme.bodySmall),
          ],
        ]));
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
        2 => !_modelIncomplete &&
            !_voiceInvalid &&
            (!voiceDownloadRequested ||
                (!voicePlanLoading &&
                    voicePlan['installable'] == true &&
                    (voicePlan['unavailable'] is List &&
                        (voicePlan['unavailable'] as List).isEmpty))),
        3 => c.review.isNotEmpty,
        _ => true,
      };

  Future<void> _primary() async {
    if (step == 3) {
      setState(() => step = 4);
      if (voiceDownloadRequested) {
        final ok = await _installVoice();
        if (!mounted) return;
        if (!ok) {
          setState(() => step = 3);
          return;
        }
        await c.run('package-review');
        if (!mounted) return;
        if (c.review.isEmpty) {
          setState(() => step = 3);
          return;
        }
      }
      await c.run('package-install');
      if (!mounted) return;
      if (c.installed) {
        await c.run('context');
        if (!mounted) return;
        if (voiceDownloadRequested && voice && _voiceComplete) {
          await _installVoiceDependencies();
          if (!mounted) return;
        }
        setState(() => step = 5);
      }
    } else if (step == 5) {
      await _openAndFinish();
    } else {
      setState(() => step++);
      if (step == 3) {
        // Automatic review: reaching this step is the request to review.
        await _reviewNow();
        if (step == 3) unawaited(_refreshVoiceState());
      }
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

  Widget _managementSurface(InstalledManageController manage) =>
      Column(children: [
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
    final managing =
        manage != null || widget.mode == 'manage' || widget.mode == 'uninstall';
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
                              Text(title, style: _theme.textTheme.titleLarge),
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
                                    onPrimary: c.installed &&
                                            !installingVoice &&
                                            !voiceDependenciesRunning
                                        ? _primary
                                        : null,
                                    secondaryLabel: c.busy ||
                                            installingVoice ||
                                            voiceDependenciesRunning
                                        ? 'Cancel installation'
                                        : 'Back',
                                    onSecondary: installingVoice ||
                                            voiceDependenciesRunning
                                        ? c.backend.cancel
                                        : c.busy
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
