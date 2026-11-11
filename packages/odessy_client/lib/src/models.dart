typedef Json = Map<String, dynamic>;

class ChatMessage {
  const ChatMessage(this.role, this.text);
  final String role, text;
}

class Session {
  Session(this.id,
      {this.selectedProfile = 'auto',
      this.activeProfile,
      this.running = false,
      List<String>? availableProfiles,
      this.engine,
      List<ChatMessage> messages = const [],
      List<Json> pending = const [],
      List<SessionEvent> events = const []})
      : availableProfiles = availableProfiles == null
            ? null
            : List.unmodifiable(availableProfiles),
        messages = List.unmodifiable(messages),
        pending = List.unmodifiable(pending.map(freezeJson)),
        events = List.unmodifiable(events);
  final String id, selectedProfile;
  final String? activeProfile;
  final bool running;

  /// Null means an older service did not provide session-scoped profiles.
  final List<String>? availableProfiles;

  /// Which backend runs this session and what it can actually do. Null for an older
  /// service; diagnostics only — a conversation must render without it.
  final EngineInfo? engine;
  final List<ChatMessage> messages;
  final List<Json> pending;
  final List<SessionEvent> events;
  factory Session.fromJson(Json json) {
    final messages = <ChatMessage>[];
    for (final turn in (json['history'] as List? ?? [])) {
      if (turn is Map) {
        if (turn['user'] is String)
          messages.add(ChatMessage('user', turn['user']));
        if (turn['assistant'] is String)
          messages.add(ChatMessage('assistant', turn['assistant']));
      }
    }
    final events = (json['events'] as List? ?? [])
        .map((e) => SessionEvent.fromJson(Map<String, dynamic>.from(e)))
        .toList();
    // Events include the in-flight user message that isn't in bounded memory yet.
    final transcripts = events.where((e) => e.type == 'transcript').toList();
    if (messages.isEmpty) {
      messages.addAll(transcripts.map((e) =>
          ChatMessage(e.data['role'] as String, e.data['text'] as String)));
    } else if (json['running'] == true &&
        transcripts.isNotEmpty &&
        transcripts.last.data['role'] == 'user') {
      messages
          .add(ChatMessage('user', transcripts.last.data['text'] as String));
    }
    return Session(json['id'] as String,
        selectedProfile: json['selected_profile'] as String,
        activeProfile: json['active_profile'] as String?,
        running: json['running'] == true,
        availableProfiles: json['available_profiles'] == null
            ? null
            : List<String>.from(json['available_profiles'] as List),
        messages: List.unmodifiable(messages),
        events: List.unmodifiable(events),
        engine: json['engine'] is Map
            ? EngineInfo.fromJson(
                Map<String, dynamic>.from(json['engine'] as Map))
            : null,
        pending: List<Json>.unmodifiable([
          ...(json['pending_approvals'] as List? ?? [])
              .map((e) => Map<String, dynamic>.from(e)),
          ...(json['pending_questions'] as List? ?? [])
              .map((e) => Map<String, dynamic>.from(e)),
        ]));
  }
}

class SessionEvent {
  SessionEvent(this.id, this.type, Json data) : data = freezeJson(data);
  final int id;
  final String type;
  final Json data;
  factory SessionEvent.fromJson(Json value) => SessionEvent(
      value['id'] as int,
      value['type'] as String,
      Map<String, dynamic>.from(value['data'] as Map? ?? {}));
}

/// Which backend runs a session, and what it can actually do (prompt 12).
///
/// Diagnostics only: `source` says whether the engine was the installation's default or
/// an explicit choice, and `capabilities` is the engine's own report for this session —
/// not a promise. An older service sends no `engine` block at all.
class EngineInfo {
  EngineInfo(
      {required this.name,
      this.source = '',
      Map<String, dynamic>? identity,
      Map<String, dynamic>? capabilities})
      : identity = Map.unmodifiable(identity ?? const {}),
        capabilities = Map.unmodifiable(capabilities ?? const {});
  final String name;
  final String source;
  final Map<String, dynamic> identity;
  final Map<String, dynamic> capabilities;

  factory EngineInfo.fromJson(Json json) => EngineInfo(
      name: json['name'] as String? ?? '',
      source: json['source'] as String? ?? '',
      identity: json['identity'] is Map
          ? Map<String, dynamic>.from(json['identity'] as Map)
          : null,
      capabilities: json['capabilities'] is Map
          ? Map<String, dynamic>.from(json['capabilities'] as Map)
          : null);

  /// Capability names this engine reports as unavailable, in stable order.
  List<String> get unsupported => capabilities.entries
      .where((entry) => entry.value is bool && entry.value == false)
      .map((entry) => entry.key)
      .toList(growable: false);

  bool supports(String capability) => capabilities[capability] == true;
}

Json freezeJson(Json value) => Map<String, dynamic>.unmodifiable(
    value.map((key, value) => MapEntry(key, _freeze(value))));
dynamic _freeze(dynamic value) => value is Map
    ? freezeJson(Map<String, dynamic>.from(value))
    : value is List
        ? List<dynamic>.unmodifiable(value.map(_freeze))
        : value;

class Profile {
  Profile(this.name, Json metadata) : metadata = freezeJson(metadata);
  final String name;
  final Json metadata;
  String? get model => metadata['model'] as String?;
  String? get provider => metadata['provider'] as String?;
}

/// Optional capability leaves existing OdessyApi implementations compatible.
abstract interface class ProfileMetadataApi {
  Future<List<Profile>> profileMetadata();
}

/// Full snapshots are authoritative; stale versions cannot replace newer state.
Session reduceSessionSnapshot(Session? current, Session incoming) {
  if (current != null && current.id != incoming.id) {
    throw StateError('Session identity changed');
  }
  int version(Session s) => s.events.fold(0, (n, e) => e.id > n ? e.id : n);
  if (current != null && version(incoming) < version(current)) return current;
  final seen = <int>{};
  final events = incoming.events.where((e) => seen.add(e.id)).toList()
    ..sort((a, b) => a.id.compareTo(b.id));
  return Session(incoming.id,
      selectedProfile: incoming.selectedProfile,
      activeProfile: incoming.activeProfile,
      running: incoming.running,
      availableProfiles: incoming.availableProfiles,
      messages: incoming.messages,
      pending: incoming.pending,
      engine: incoming.engine,
      events: events);
}
