import 'dart:convert';
import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';
import 'interaction_card.dart';
import '../../../widgets/pixel_boat.dart';
import '../../../app/readiness_controller.dart';
import '../controllers/voice_controller.dart';

class ConversationPage extends StatefulWidget {
  const ConversationPage(
      {super.key, required this.controller, this.readiness, this.voice});
  final VoiceController? voice;
  final ReadinessController? readiness;
  final DesktopSession controller;
  @override
  State<ConversationPage> createState() => _ConversationPageState();
}

class _ConversationPageState extends State<ConversationPage> {
  final input = TextEditingController();
  final scroll = ScrollController();
  @override
  void dispose() {
    input.dispose();
    scroll.dispose();
    super.dispose();
  }

  Future<void> send() async {
    if (input.text.trim().isEmpty) return;
    final text = input.text;
    if (await widget.controller.send(text)) {
      if (!mounted) return;
      input.clear();
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (scroll.hasClients) {
          scroll.animateTo(scroll.position.maxScrollExtent,
              duration: const Duration(milliseconds: 200),
              curve: Curves.easeOut);
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller, session = c.session;
    return ListenableBuilder(
        listenable: widget.voice ?? c,
        builder: (context, _) => Column(children: [
              Expanded(
                  child: SelectionArea(
                      child: ListView(
                          controller: scroll,
                          padding: const EdgeInsets.fromLTRB(32, 12, 32, 24),
                          children: [
                    Padding(
                        padding: const EdgeInsets.fromLTRB(0, 10, 0, 18),
                        child: Wrap(
                            spacing: 20,
                            runSpacing: 12,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                        c.connected
                                            ? 'Your workspace, connected.'
                                            : 'Your workspace, offline.',
                                        style: const TextStyle(
                                            fontSize: 20,
                                            fontWeight: FontWeight.w600,
                                            letterSpacing: -.4)),
                                    const SizedBox(height: 5),
                                    if (widget.readiness?.current
                                            ?.configuration !=
                                        null)
                                      Text(
                                          'Startup model: ${widget.readiness!.current!.configuration!.modelName}',
                                          style: const TextStyle(fontSize: 12)),
                                    Text(
                                        'Coordinator: ${c.api.identity}  ·  Execution: backend policy',
                                        style: const TextStyle(
                                            fontSize: 11, color: Colors.grey))
                                  ]),
                              SizedBox(
                                  width: 190,
                                  child: DropdownButtonFormField<String>(
                                      key: ValueKey(session?.selectedProfile),
                                      initialValue: c.profiles.contains(
                                              session?.selectedProfile)
                                          ? session?.selectedProfile
                                          : null,
                                      isExpanded: true,
                                      decoration: const InputDecoration(
                                          labelText: 'Next-turn model',
                                          contentPadding: EdgeInsets.symmetric(
                                              horizontal: 12, vertical: 8)),
                                      items: c.profiles
                                          .map((p) => DropdownMenuItem(
                                              value: p,
                                              child: Tooltip(
                                                  message: c.profileLabel(p),
                                                  child: Text(c.profileLabel(p),
                                                      overflow: TextOverflow
                                                          .ellipsis))))
                                          .toList(),
                                      onChanged: c.busy || !c.connected
                                          ? null
                                          : (value) {
                                              if (value != null) {
                                                c.select(value);
                                              }
                                            })),
                            ])),
                    if (c.busy && session == null)
                      const Center(
                          child: Padding(
                              padding: EdgeInsets.all(40),
                              child: CircularProgressIndicator())),
                    if (session?.messages.isEmpty ?? true) _welcome(context),
                    for (final message in session?.messages ?? [])
                      Padding(
                          padding: const EdgeInsets.symmetric(vertical: 15),
                          child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Container(
                                    width: 30,
                                    height: 30,
                                    decoration: BoxDecoration(
                                        color: Theme.of(context)
                                            .colorScheme
                                            .primary
                                            .withValues(alpha: .12),
                                        borderRadius: BorderRadius.circular(9)),
                                    child: Center(
                                        child: message.role == 'user'
                                            ? const Icon(Icons.person_outline,
                                                size: 17)
                                            : const PixelBoat())),
                                const SizedBox(width: 14),
                                Expanded(
                                    child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                      Text(
                                          message.role == 'user'
                                              ? 'You'
                                              : 'Odessy',
                                          style: const TextStyle(
                                              fontSize: 12,
                                              fontWeight: FontWeight.w600)),
                                      const SizedBox(height: 8),
                                      Text(message.text,
                                          style: const TextStyle(height: 1.6))
                                    ])),
                              ])),
                    for (final entry in session?.pending ?? [])
                      InteractionCard(
                          key: ValueKey(entry['id']),
                          entry: entry,
                          controller: c),
                    if (session?.running == true && session!.pending.isEmpty)
                      const Padding(
                          padding: EdgeInsets.symmetric(vertical: 20),
                          child: Row(children: [
                            SizedBox(
                                width: 12,
                                height: 12,
                                child:
                                    CircularProgressIndicator(strokeWidth: 2)),
                            SizedBox(width: 12),
                            Text('Working on your request…',
                                style:
                                    TextStyle(color: Colors.grey, fontSize: 12))
                          ])),
                    if (session != null && session.events.isNotEmpty)
                      ExpansionTile(
                          tilePadding: EdgeInsets.zero,
                          title: const Text('Activity & provenance',
                              style:
                                  TextStyle(fontSize: 12, color: Colors.grey)),
                          children: [
                            for (final event
                                in session.events.reversed.take(12))
                              ListTile(
                                  dense: true,
                                  contentPadding: EdgeInsets.zero,
                                  title: Text(event.type,
                                      style: const TextStyle(fontSize: 11)),
                                  subtitle: Text(jsonEncode(event.data),
                                      maxLines: 3,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(
                                          fontSize: 10, color: Colors.grey))),
                          ]),
                  ]))),
              Padding(
                  padding: const EdgeInsets.fromLTRB(32, 8, 32, 20),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (c.route.isNotEmpty)
                          Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: Text(c.route,
                                  style: const TextStyle(
                                      fontSize: 10, color: Colors.grey))),
                        Card(
                            child: Padding(
                                padding: const EdgeInsets.fromLTRB(16, 6, 8, 6),
                                child: Row(
                                    crossAxisAlignment: CrossAxisAlignment.end,
                                    children: [
                                      Expanded(
                                          child: TextField(
                                              key: const Key('composer'),
                                              controller: input,
                                              minLines: 1,
                                              maxLines: 4,
                                              textInputAction:
                                                  TextInputAction.send,
                                              enabled: c.connected &&
                                                  !(session?.running ??
                                                      false) &&
                                                  !c.busy,
                                              decoration: const InputDecoration(
                                                  hintText:
                                                      'Ask Odessy anything…',
                                                  fillColor: Colors.transparent,
                                                  border: InputBorder.none,
                                                  enabledBorder:
                                                      InputBorder.none),
                                              onSubmitted: (_) => send())),
                                      IconButton(
                                          tooltip: widget.voice?.recording ==
                                                  true
                                              ? 'Finish recording and submit speech'
                                              : 'Record on the coordinator computer',
                                          onPressed: widget.voice?.supported ==
                                                      true &&
                                                  widget.voice?.busy == false &&
                                                  widget.voice?.snapshot
                                                          ?.mode !=
                                                      'wake'
                                              ? () => widget.voice!.command(
                                                  widget.voice!.recording
                                                      ? 'finish_capture'
                                                      : 'start_capture')
                                              : null,
                                          icon: Icon(
                                              widget.voice?.recording == true
                                                  ? Icons.stop_circle_outlined
                                                  : Icons.mic_none,
                                              size: 20)),
                                      if (widget.voice?.snapshot?.captureId !=
                                          null)
                                        IconButton(
                                            tooltip:
                                                'Cancel recording without submitting',
                                            onPressed: () => widget.voice!
                                                .command('cancel_capture'),
                                            icon: const Icon(Icons.close)),
                                      IconButton.filled(
                                          tooltip: session?.running == true
                                              ? 'Stop current turn'
                                              : 'Send message',
                                          onPressed: !c.connected || c.stopping
                                              ? null
                                              : session?.running == true
                                                  ? c.stop
                                                  : c.busy
                                                      ? null
                                                      : send,
                                          icon: Icon(
                                              session?.running == true
                                                  ? Icons.stop_rounded
                                                  : Icons.arrow_upward_rounded,
                                              size: 20)),
                                    ]))),
                        const SizedBox(height: 10),
                        if (widget.voice != null && !c.api.isDemo) ...[
                          Text(
                              'Voice: ${widget.voice!.snapshot?.state ?? 'unavailable'} · Records on ${widget.voice!.snapshot?.host ?? 'coordinator computer'}',
                              style: const TextStyle(fontSize: 11)),
                          if (widget.voice!.error.isNotEmpty)
                            Text(widget.voice!.error),
                          if (widget.voice!.snapshot?.message.isNotEmpty ==
                              true)
                            Text(widget.voice!.snapshot!.message),
                          Wrap(children: [
                            TextButton(
                                onPressed: widget.voice!.refresh,
                                child: const Text('Check voice availability')),
                            if (widget.voice!.supported)
                              TextButton(
                                  onPressed: widget.voice!.busy &&
                                          widget.voice!.snapshot?.mode != 'wake'
                                      ? null
                                      : () => widget.voice!.command(
                                          widget.voice!.snapshot?.mode == 'wake'
                                              ? 'disable_wake'
                                              : 'enable_wake'),
                                  child: Text(widget.voice!.snapshot?.mode ==
                                          'wake'
                                      ? 'Stop wake listening'
                                      : 'Enable wake listening on coordinator')),
                            if (widget.voice!.supported &&
                                widget.voice!.snapshot?.mode != 'wake' &&
                                widget.voice!.busy == false &&
                                !widget.voice!.recording) ...[
                              TextButton(
                                  onPressed: () =>
                                      widget.voice!.command('test_input'),
                                  child: const Text(
                                      'Test speech input · no model call')),
                              TextButton(
                                  onPressed: () =>
                                      widget.voice!.command('test_output'),
                                  child: const Text('Test speech output')),
                            ],
                            if (widget.voice!.supported)
                              TextButton(
                                  onPressed: () =>
                                      widget.voice!.command('stop_playback'),
                                  child: const Text('Stop playback'))
                          ]),
                        ],
                        Row(children: [
                          Icon(Icons.circle,
                              size: 5,
                              color: c.connected
                                  ? const Color(0xff9bb9a7)
                                  : Colors.orange),
                          const SizedBox(width: 7),
                          Expanded(
                              child: Text(c.status,
                                  style: const TextStyle(
                                      fontSize: 10, color: Colors.grey))),
                          if (c.api.isDemo)
                            const Text('No model or tool executes in demo',
                                style:
                                    TextStyle(fontSize: 10, color: Colors.grey))
                        ]),
                      ])),
            ]));
  }

  Widget _welcome(BuildContext context) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 45),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(Icons.navigation_outlined,
            size: 38, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: 24),
        Text('A little less friction.\nA little more possibility.',
            style: Theme.of(context)
                .textTheme
                .titleLarge
                ?.copyWith(fontSize: 31, height: 1.3)),
        const SizedBox(height: 15),
        const Text(
            'Think, build, and work with an assistant under your control.',
            style: TextStyle(color: Colors.grey, fontSize: 13)),
        const SizedBox(height: 25),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final prompt in widget.controller.api.isDemo
              ? ['Plan my next task', '/approval', '/question']
              : ['Hello', 'What time is it?'])
            ActionChip(
                label: Text(prompt, style: const TextStyle(fontSize: 11)),
                onPressed: () {
                  input.text = prompt;
                }),
        ]),
        if (widget.controller.api.isDemo)
          const Padding(
              padding: EdgeInsets.only(top: 24),
              child: Text(
                  'Demo fixtures · Try /approval or /question to inspect human interactions.\nUse the connection button above for a real local session.',
                  style: TextStyle(
                      fontSize: 11, height: 1.7, color: Colors.grey))),
      ]));
}
