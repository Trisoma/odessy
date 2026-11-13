import 'models.dart';

/// Live sessions only. A new service instance invalidates saved session IDs.
abstract interface class SessionDirectoryApi {
  Future<SessionDirectoryPage> listSessions({int limit = 50, int offset = 0});
}

class SessionSummary {
  SessionSummary.fromJson(Json value) {
    if (value['id'] is! String ||
        (value['id'] as String).isEmpty ||
        value['selected_profile'] is! String ||
        !const ['legacy', 'harness'].contains(value['engine']) ||
        value['running'] is! bool) {
      throw const FormatException('Invalid session summary');
    }
    id = value['id'] as String;
    engine = value['engine'] as String;
    selectedProfile = value['selected_profile'] as String;
    running = value['running'] as bool;
  }
  late final String id, engine, selectedProfile;
  late final bool running;
}

class SessionDirectoryPage {
  SessionDirectoryPage.fromJson(Json value) {
    final instance = value['service_instance_id'];
    final rows = value['sessions'];
    final next = value['next_offset'];
    if (instance is! String ||
        instance.isEmpty ||
        rows is! List ||
        rows.length > 100 ||
        rows.any((row) => row is! Json) ||
        (next != null && (next is! int || next < 1 || next > 100000))) {
      throw const FormatException('Invalid session directory');
    }
    serviceInstanceId = instance;
    sessions = List.unmodifiable(
        rows.map((row) => SessionSummary.fromJson(row as Json)));
    if (sessions.map((s) => s.id).toSet().length != sessions.length) {
      throw const FormatException('Duplicate session identity');
    }
    nextOffset = next as int?;
  }
  late final String serviceInstanceId;
  late final List<SessionSummary> sessions;
  late final int? nextOffset;
}
