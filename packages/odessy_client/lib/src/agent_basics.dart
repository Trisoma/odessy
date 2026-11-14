/// Optional agent-basics contracts. Existing OdessyApi implementations are unchanged.
library;

import 'models.dart';

enum AccessMode { ask, auto, full }

class AgentBasicsCapabilities {
  const AgentBasicsCapabilities({
    this.conversations = false,
    this.customContinuation = false,
    this.harnessContinuation = false,
    this.accessModes = false,
    this.context = false,
    this.memory = false,
    this.retrieval = false,
    this.terminal = false,
    this.checkpoints = false,
  });
  final bool conversations,
      customContinuation,
      harnessContinuation,
      accessModes,
      context,
      memory,
      retrieval,
      terminal,
      checkpoints;
}

/// Wire responses follow agent-basics contract v1. The implementing client must
/// validate every response before exposing it; Json is not permission authority.
abstract interface class ConversationApi {
  Future<Json> conversations({
    String? projectId,
    bool archived = false,
    String? cursor,
    int limit = 50,
  });
  Future<Json> createConversation({
    required String projectId,
    String title = 'New chat',
  });
  Future<Json> conversation(String id);
  Future<Json> conversationMessages(String id, {int? before, int limit = 100});
  Future<Json> searchConversations(
    String query, {
    String? projectId,
    String? cursor,
    int limit = 50,
  });
  Future<Json> updateConversation(
    String id, {
    required int revision,
    String? title,
    bool? pinned,
    bool? archived,
  });
  Future<Json> exportConversation(String id);
  Future<void> deleteConversation(String id, {required int revision});
  Future<Json> resumeConversation(String id, {required int revision});
}

abstract interface class AccessModeApi {
  Future<Json> accessMode(String sid);
  Future<Json> setAccessMode(
    String sid, {
    required AccessMode mode,
    required int revision,
    required String serviceInstanceId,
  });
}

abstract interface class ContextApi {
  Future<Json> usedContext(String conversationId);
  Future<Json> memory(String projectId);
  Future<Json> createMemory(String projectId, {required String text});
  Future<Json> updateMemory(
    String projectId,
    String id, {
    required int revision,
    String? text,
    bool? enabled,
  });
  Future<void> deleteMemory(
    String projectId,
    String id, {
    required int revision,
  });
  Future<Json> documents(String projectId);
  Future<Json> indexDocument(
    String projectId, {
    required String sessionId,
    required String serviceInstanceId,
    required String path,
  });
  Future<void> removeDocument(
    String projectId,
    String id, {
    required int revision,
  });
  Future<Json> retrieve(
    String projectId, {
    required String sessionId,
    required String query,
    int limit = 5,
  });
}

abstract interface class TerminalApi {
  Future<Json> createTerminal(String sid, {int columns = 80, int rows = 24});
  Future<Json> terminalOutput(String sid, String id, {int after = 0});
  Future<void> terminalInput(
    String sid,
    String id, {
    required String text,
    required int revision,
  });
  Future<Json> resizeTerminal(
    String sid,
    String id, {
    required int columns,
    required int rows,
    required int revision,
  });
  Future<Json> interruptTerminal(
    String sid,
    String id, {
    required int revision,
  });
  Future<Json> closeTerminal(String sid, String id, {required int revision});
}

abstract interface class CheckpointApi {
  Future<Json> checkpoints(String sid);
  Future<Json> createCheckpoint(String sid, {required String label});
  Future<Json> previewCheckpoint(String sid, String id);
  Future<Json> restoreCheckpoint(
    String sid,
    String id, {
    required String previewHash,
    required int revision,
  });
}
