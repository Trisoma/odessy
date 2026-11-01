import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../../app/session_controller.dart';
import '../../../app/readiness_controller.dart';
import '../controllers/voice_controller.dart';
import 'interaction_card.dart';
import 'message_content.dart';
import 'session_activity.dart';

class ConversationPage extends StatefulWidget {
  const ConversationPage(
      {super.key,
      required this.controller,
      this.readiness,
      this.voice,
      this.onOpenModels});
  final DesktopSession controller;
  final ReadinessController? readiness;
  final VoiceController? voice;
  final VoidCallback? onOpenModels;
  @override
  State<ConversationPage> createState() => _ConversationPageState();
}

class _ConversationPageState extends State<ConversationPage> {
  final input = TextEditingController();
  final scroll = ScrollController();
  bool submitting = false, showLatest = false, showVoiceOptions = false;
  Object? contentStamp;
  int scrollEpoch = 0;
  late VoiceController voice;
  bool _ownsVoice = false;

  void _bindVoice() {
    _ownsVoice = widget.voice == null;
    voice = widget.voice ?? VoiceController(widget.controller);
    voice.addListener(_voiceChanged);
  }

  @override
  void initState() {
    super.initState();
    input.addListener(_draftChanged);
    scroll.addListener(_scrolled);
    widget.controller.addListener(_sessionChanged);
    _bindVoice();
    contentStamp = _stamp();
  }

  @override
  void didUpdateWidget(covariant ConversationPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller ||
        oldWidget.voice != widget.voice) {
      voice.removeListener(_voiceChanged);
      if (_ownsVoice) voice.dispose();
      _bindVoice();
      oldWidget.controller.removeListener(_sessionChanged);
      widget.controller.addListener(_sessionChanged);
      contentStamp = _stamp();
    }
  }

  Object _stamp() {
    final s = widget.controller.session;
    return (
      s?.id,
      s?.messages.length,
      s?.messages.lastOrNull?.text,
      s?.pending.length,
      s?.events.lastOrNull?.id
    );
  }

  void _draftChanged() => setState(() {});
  void _voiceChanged() {
    if (mounted) setState(() {});
  }

  void _scrolled() {
    final next = scroll.hasClients && scroll.position.extentAfter > 80;
    if (next != showLatest) setState(() => showLatest = next);
  }

  void _sessionChanged() {
    final next = _stamp();
    final follow = scroll.hasClients && scroll.position.extentAfter < 80;
    final changed = next != contentStamp;
    contentStamp = next;
    setState(() {});
    if (changed && follow) _latest();
  }

  void _latest() {
    final epoch = ++scrollEpoch;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_settleAtLatest(epoch));
    });
  }

  Future<void> _settleAtLatest(int epoch) async {
    // Lazy message layouts can revise the end position during the animation.
    for (var attempt = 0; attempt < 4; attempt++) {
      if (!mounted || !scroll.hasClients || epoch != scrollEpoch) return;
      if (MediaQuery.disableAnimationsOf(context)) {
        scroll.jumpTo(scroll.position.maxScrollExtent);
        await WidgetsBinding.instance.endOfFrame;
      } else {
        await scroll.animateTo(scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 180), curve: Curves.easeOut);
      }
      if (!mounted ||
          !scroll.hasClients ||
          epoch != scrollEpoch ||
          (scroll.position.extentAfter <= 1 &&
              !scroll.position.isScrollingNotifier.value)) {
        return;
      }
      await WidgetsBinding.instance.endOfFrame;
    }
  }

  bool get canSend {
    final c = widget.controller;
    return c.connected &&
        c.session != null &&
        !c.busy &&
        !c.stopping &&
        !submitting &&
        c.session?.running != true &&
        input.text.trim().isNotEmpty;
  }

  Future<void> send() async {
    if (!canSend ||
        (input.value.composing.isValid && !input.value.composing.isCollapsed)) {
      return;
    }
    final text = input.text, c = widget.controller;
    setState(() => submitting = true);
    try {
      if (await c.send(text)) {
        if (!mounted) return;
        if (input.text == text) input.clear();
        _latest();
      }
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_sessionChanged);
    voice.removeListener(_voiceChanged);
    if (_ownsVoice) voice.dispose();
    input.removeListener(_draftChanged);
    scroll.removeListener(_scrolled);
    input.dispose();
    scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      NotificationListener<ScrollStartNotification>(
          onNotification: (notification) {
        if (notification.dragDetails != null) scrollEpoch++;
        return false;
      }, child: LayoutBuilder(builder: (context, box) {
        final inset = box.maxWidth < 600 ? 16.0 : 32.0;
        final compact = box.maxHeight < 600 ||
            (box.maxWidth < 600 &&
                MediaQuery.textScalerOf(context).scale(14) > 20);
        final header = Padding(
            padding: EdgeInsets.fromLTRB(inset, 16, inset, 12),
            child: _content(_header(context)));
        final composer = Padding(
            padding: EdgeInsets.fromLTRB(inset, 8, inset, 16),
            child: _content(_composer(context, narrow: box.maxWidth < 600)));
        if (compact) {
          return SelectionArea(
              child: SingleChildScrollView(
            key: const Key('conversation-transcript'),
            controller: scroll,
            child: Column(children: [
              header,
              const Divider(height: 1),
              Padding(
                  padding: EdgeInsets.fromLTRB(inset, 20, inset, 24),
                  child: Column(children: _entries(context))),
              composer
            ]),
          ));
        }
        return Column(children: [
          header,
          const Divider(height: 1),
          Expanded(
              child: Stack(children: [
            SelectionArea(
                child: ListView(
                    key: const Key('conversation-transcript'),
                    controller: scroll,
                    padding: EdgeInsets.fromLTRB(inset, 20, inset, 24),
                    children: _entries(context))),
            if (showLatest)
              Positioned(
                  bottom: 12,
                  right: inset,
                  child: FilledButton.tonalIcon(
                      onPressed: _latest,
                      icon: const Icon(Icons.arrow_downward, size: 16),
                      label: const Text('Latest messages'))),
          ])),
          composer,
        ]);
      }));

  List<Widget> _entries(BuildContext context) {
    final c = widget.controller, s = c.session;
    return [
      if (c.busy && s == null)
        const Padding(
            padding: EdgeInsets.all(24),
            child: Center(
                child: CircularProgressIndicator(
                    semanticsLabel: 'Connecting to session service')))
      else if (!c.connected || c.error.isNotEmpty)
        _connectionNotice(context),
      if ((s?.messages.isEmpty ?? true) &&
          (s?.pending.isEmpty ?? true) &&
          s?.running != true &&
          !(c.busy && s == null))
        _welcome(context),
      for (final message in s?.messages ?? [])
        _content(_message(context, message.role, message.text)),
      for (final entry in s?.pending ?? [])
        _content(InteractionCard(
            key: ValueKey(entry['id']), entry: entry, controller: c)),
      if (s?.running == true && s!.pending.isEmpty)
        _content(Padding(
            padding: const EdgeInsets.symmetric(vertical: 16),
            child: Row(children: [
              const SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 2)),
              const SizedBox(width: 12),
              Expanded(
                  child: Text('Working on your request…',
                      style: Theme.of(context).textTheme.bodySmall)),
            ]))),
      if (s != null && s.events.isNotEmpty)
        _content(SessionActivity(events: s.events)),
    ];
  }

  void _newLine() {
    final c = widget.controller;
    if (!c.connected ||
        c.busy ||
        c.stopping ||
        submitting ||
        c.session?.running == true ||
        (input.value.composing.isValid && !input.value.composing.isCollapsed)) {
      return;
    }
    final value = input.value;
    final start =
        value.selection.isValid ? value.selection.start : value.text.length;
    final end =
        value.selection.isValid ? value.selection.end : value.text.length;
    input.value = TextEditingValue(
        text: value.text.replaceRange(start, end, '\n'),
        selection: TextSelection.collapsed(offset: start + 1));
  }

  Widget _content(Widget child) => Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: SizedBox(width: double.infinity, child: child)));

  Widget _header(BuildContext context) {
    final c = widget.controller, s = c.session;
    final details =
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Wrap(
          spacing: 12,
          runSpacing: 4,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Semantics(
                header: true,
                child: Text('Session',
                    style: Theme.of(context).textTheme.titleMedium)),
            Text(
                c.busy && s == null
                    ? 'Connecting'
                    : c.connected
                        ? 'Connected'
                        : 'Offline',
                style: Theme.of(context).textTheme.bodySmall),
            IconButton(
                tooltip: 'Session details',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.info_outline, size: 18),
                onPressed: () => showDialog<void>(
                    context: context,
                    builder: (context) => AlertDialog(
                            title: const Text('Session details'),
                            content: SelectableText(
                                'Coordinator: ${c.api.identity}'),
                            actions: [
                              TextButton(
                                  onPressed: () => Navigator.pop(context),
                                  child: const Text('Close'))
                            ]))),
          ]),
      if (s?.running == true && s?.activeProfile != null)
        Text('Active model: ${s!.activeProfile}',
            style: Theme.of(context).textTheme.bodySmall),
    ]);
    final model = DropdownButtonFormField<String>(
      key: ValueKey((c.api, s?.selectedProfile)),
      initialValue:
          c.profiles.contains(s?.selectedProfile) ? s?.selectedProfile : null,
      isExpanded: true,
      decoration: const InputDecoration(
          labelText: 'Next-turn model',
          contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10)),
      items: c.profiles
          .map((p) => DropdownMenuItem(
              value: p, child: Text(p, overflow: TextOverflow.ellipsis)))
          .toList(),
      onChanged: c.busy || c.stopping || !c.connected
          ? null
          : (value) {
              if (value != null) unawaited(c.select(value));
            },
    );
    return LayoutBuilder(builder: (context, box) {
      if (box.maxWidth < 600 ||
          MediaQuery.textScalerOf(context).scale(14) > 20) {
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [details, const SizedBox(height: 12), model]);
      }
      return Row(children: [
        Expanded(child: details),
        const SizedBox(width: 24),
        SizedBox(width: 200, child: model)
      ]);
    });
  }

  Widget _message(BuildContext context, String role, String text) {
    final user = role == 'user';
    final label = switch (role) {
      'user' => 'You',
      'assistant' => 'Odessy',
      'tool' => 'Tool output',
      'system' => 'System',
      _ => role,
    };
    final copy = IconButton(
        tooltip: 'Copy message',
        visualDensity: VisualDensity.compact,
        icon: const Icon(Icons.copy_outlined, size: 16),
        onPressed: () async {
          await Clipboard.setData(ClipboardData(text: text));
          if (context.mounted) {
            ScaffoldMessenger.of(context)
                .showSnackBar(const SnackBar(content: Text('Message copied')));
          }
        });
    final labelStyle = Theme.of(context)
        .textTheme
        .labelLarge
        ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant);

    if (user) {
      return LayoutBuilder(builder: (context, bounds) {
        final maxWidth = bounds.maxWidth * .78;
        return Padding(
            padding: const EdgeInsets.only(bottom: 24),
            child: Align(
                alignment: Alignment.centerRight,
                child: ConstrainedBox(
                    constraints: BoxConstraints(maxWidth: maxWidth),
                    child: IntrinsicWidth(
                        child: Container(
                            key: const Key('user-message-bubble'),
                            padding: const EdgeInsets.fromLTRB(16, 8, 8, 14),
                            decoration: BoxDecoration(
                                color: Theme.of(context)
                                    .colorScheme
                                    .surfaceContainerHighest,
                                borderRadius: BorderRadius.circular(18)),
                            child: Column(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Text(label, style: labelStyle),
                                        const SizedBox(width: 8),
                                        copy,
                                      ]),
                                  Padding(
                                      padding: const EdgeInsets.only(right: 8),
                                      child: Text(text,
                                          style: const TextStyle(height: 1.6))),
                                ]))))));
      });
    }

    return Padding(
        padding: const EdgeInsets.only(bottom: 24),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Icon(
                  role == 'tool' ? Icons.terminal : Icons.bubble_chart_outlined,
                  size: 20,
                  color: Theme.of(context).colorScheme.primary)),
          const SizedBox(width: 12),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Row(children: [
                  Expanded(child: Text(label, style: labelStyle)),
                  copy,
                ]),
                if (role == 'assistant')
                  MessageContent(text: text)
                else
                  Text(text, style: const TextStyle(height: 1.6)),
              ])),
        ]));
  }

  Widget _composer(BuildContext context, {required bool narrow}) {
    final c = widget.controller, running = c.session?.running == true;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (c.route.isNotEmpty)
        Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(c.route, style: Theme.of(context).textTheme.bodySmall)),
      Card(
          child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
              child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                Expanded(
                    child: CallbackShortcuts(
                        bindings: {
                      const SingleActivator(LogicalKeyboardKey.enter,
                          shift: true): _newLine,
                      const SingleActivator(LogicalKeyboardKey.numpadEnter,
                          shift: true): _newLine,
                      const SingleActivator(LogicalKeyboardKey.enter): () =>
                          unawaited(send()),
                      const SingleActivator(LogicalKeyboardKey.numpadEnter):
                          () => unawaited(send()),
                    },
                        child: TextField(
                            key: const Key('composer'),
                            controller: input,
                            minLines: 1,
                            maxLines: narrow ? 3 : 5,
                            textInputAction: TextInputAction.newline,
                            enabled: c.connected &&
                                !running &&
                                !c.busy &&
                                !c.stopping &&
                                !submitting,
                            decoration: const InputDecoration(
                                hintText: 'Message Odessy…',
                                labelText: 'Message',
                                filled: false,
                                border: InputBorder.none,
                                enabledBorder: InputBorder.none)))),
                IconButton(
                    tooltip: voice.recording
                        ? 'Finish recording and submit speech'
                        : 'Record on the coordinator computer',
                    onPressed: voice.supported &&
                            !voice.busy &&
                            voice.snapshot?.mode != 'wake'
                        ? () => voice.command(voice.recording
                            ? 'finish_capture'
                            : 'start_capture')
                        : null,
                    icon: Icon(
                        voice.recording
                            ? Icons.stop_circle_outlined
                            : Icons.mic_none,
                        size: 20)),
                if (voice.snapshot?.captureId != null)
                  IconButton(
                      tooltip: 'Cancel recording without submitting',
                      onPressed: () => voice.command('cancel_capture'),
                      icon: const Icon(Icons.close)),
                IconButton.filled(
                    key: const Key('send-or-stop'),
                    tooltip: running ? 'Stop current turn' : 'Send message',
                    onPressed: !c.connected || c.stopping
                        ? null
                        : running
                            ? c.stop
                            : canSend
                                ? send
                                : null,
                    icon: Icon(
                        running
                            ? Icons.stop_rounded
                            : Icons.arrow_upward_rounded,
                        size: 20)),
              ]))),
      const SizedBox(height: 8),
      Wrap(spacing: 16, runSpacing: 4, children: [
        Text(c.status, style: Theme.of(context).textTheme.bodySmall),
        Text(
            running
                ? 'Stop does not undo completed actions.'
                : 'Enter to send · Shift+Enter for a new line',
            style: Theme.of(context).textTheme.bodySmall),
        if (c.api.isDemo)
          Text('Demo · no model or tool executes',
              style: Theme.of(context).textTheme.bodySmall),
        if (voice.snapshot?.mode == 'wake')
          Semantics(
              liveRegion: true,
              child: Text('Wake listening',
                  style: Theme.of(context).textTheme.bodySmall)),
        if (const {
          'preparing',
          'recording',
          'transcribing',
          'speaking',
          'cancelling'
        }.contains(voice.snapshot?.state))
          Semantics(
              liveRegion: true,
              child: Text('Audio: ${voice.snapshot!.state}',
                  style: Theme.of(context).textTheme.bodySmall)),
        TextButton.icon(
            style: TextButton.styleFrom(
                visualDensity: VisualDensity.compact,
                padding: const EdgeInsets.symmetric(horizontal: 8)),
            onPressed: () =>
                setState(() => showVoiceOptions = !showVoiceOptions),
            icon: Icon(showVoiceOptions ? Icons.expand_less : Icons.expand_more,
                size: 18),
            label: const Text('Voice options')),
        if (!showVoiceOptions &&
            voice.supported &&
            voice.snapshot?.mode == 'wake')
          TextButton(
              onPressed: () => voice.command('disable_wake'),
              child: const Text('Stop wake listening')),
        if (!showVoiceOptions &&
            voice.supported &&
            voice.snapshot?.state == 'speaking')
          TextButton(
              onPressed: () => voice.command('stop_playback'),
              child: const Text('Stop playback')),
      ]),
      if (voice.error.isNotEmpty)
        Semantics(liveRegion: true, child: Text(voice.error)),
      if (showVoiceOptions) ...[
        if (voice.snapshot?.message.isNotEmpty == true)
          Text(voice.snapshot!.message,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant)),
        Wrap(spacing: 8, runSpacing: 4, children: [
          TextButton(
              onPressed: voice.refresh,
              child: const Text('Check voice availability')),
          if (voice.supported)
            TextButton(
                onPressed: voice.busy && voice.snapshot?.mode != 'wake'
                    ? null
                    : () => voice.command(voice.snapshot?.mode == 'wake'
                        ? 'disable_wake'
                        : 'enable_wake'),
                child: Text(voice.snapshot?.mode == 'wake'
                    ? 'Stop wake listening'
                    : 'Enable wake listening on coordinator')),
          if (voice.supported &&
              voice.snapshot?.mode != 'wake' &&
              !voice.busy &&
              !voice.recording) ...[
            TextButton(
                onPressed: () => voice.command('test_input'),
                child: const Text('Test speech input · no model call')),
            TextButton(
                onPressed: () => voice.command('test_output'),
                child: const Text('Test speech output')),
          ],
          if (voice.supported)
            TextButton(
                onPressed: () => voice.command('stop_playback'),
                child: const Text('Stop playback')),
        ]),
      ],
    ]);
  }

  Widget _welcome(BuildContext context) => _content(Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('What would you like to work on?',
            style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 8),
        const Text(
            'Ask a question, inspect your workspace, or plan your next step.'),
        const SizedBox(height: 16),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final prompt in widget.controller.api.isDemo
              ? ['Plan my next task', '/approval', '/question']
              : ['Hello', 'What time is it?'])
            ActionChip(
                label: Text(prompt),
                onPressed: widget.controller.connected
                    ? () {
                        input.text = prompt;
                      }
                    : null),
        ]),
      ])));

  Widget _connectionNotice(BuildContext context) {
    final c = widget.controller;
    final guidance = modelErrorGuidance(c.error);
    return _content(Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Card(
            child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                          c.error.isNotEmpty
                              ? 'Session needs attention'
                              : 'Service offline',
                          style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 8),
                      Semantics(
                          liveRegion: true,
                          child: Text(c.error.isNotEmpty
                              ? guidance ?? c.error
                              : 'Connect to your local service to continue.')),
                      const SizedBox(height: 12),
                      Wrap(spacing: 8, runSpacing: 8, children: [
                        OutlinedButton.icon(
                            onPressed: c.busy || c.stopping
                                ? null
                                : () async {
                                    if (c.session == null) {
                                      await c.start();
                                    } else {
                                      await c.refresh();
                                    }
                                  },
                            icon: const Icon(Icons.refresh, size: 16),
                            label: const Text('Refresh session')),
                        if (guidance != null && widget.onOpenModels != null)
                          TextButton(
                              onPressed: widget.onOpenModels,
                              child: const Text('Open model settings')),
                      ]),
                    ])))));
  }
}

/// Provides actionable copy for recognized model failures without changing
/// the service error retained by the session controller.
String? modelErrorGuidance(String error) {
  final lower = error.toLowerCase();
  if (!lower.contains('ollama')) return null;
  if (lower.contains('10061') || lower.contains('connection refused')) {
    return 'Cannot connect to Ollama. Start the model service or choose another model in Settings.';
  }
  if (lower.contains('usage limit') ||
      lower.contains('quota') ||
      lower.contains('429')) {
    return 'The model service reported a usage limit. Check the provider account or choose another model.';
  }
  if (lower.contains('model') &&
      (lower.contains('not found') || lower.contains('not-configured'))) {
    return 'The selected model is unavailable. Check the model name and configure an available model in Settings.';
  }
  return null;
}
