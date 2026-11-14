"""Application-owned bridge between durable data and fresh live authority."""
from __future__ import annotations

import inspect
import json
import os
import uuid
from pathlib import Path

from .agent_basics import BasicsDependencies, PreparedContext
from .access_modes import AccessModeService
from .access_mode_routes import register as register_access_modes
from .conversations import build_service, settle_after_restart
from .conversations.routes import register as register_conversations
from .conversations.lifecycle import ConversationRecorder
from .conversations.store import Conflict, NotFound, _check_int, default_store_path


class PersistentComposition:
    def __init__(self, app, find, factory, *, config_path=None):
        self.app, self.factory = app, factory
        self.dependencies = BasicsDependencies(app.state.service_instance_id, find)
        path = Path(config_path).parent / 'conversations.sqlite3' if config_path else default_store_path()
        self.resume_allowed = lambda: True
        # 04 supplies the frozen ContextProvider; 06/07 mount its routes/capability.
        self.context_provider = None
        self.service = build_service(path=path, continuation=self.resume,
                                     settings=self.settings)
        with self.service.store.connection() as db:
            with db:
                db.execute('''CREATE TABLE IF NOT EXISTS conversation_workspace_bindings (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                    identity_json TEXT NOT NULL)''')
                db.execute("INSERT OR IGNORE INTO schema_extensions(name,version) VALUES ('conversation_workspace_binding',1)")
        settle_after_restart(self.service.store)
        self.modes = AccessModeService(self.dependencies, resolve_scope=self.scope)
        app.state.conversations = self.service
        register_conversations(app, self.dependencies, service=self.service)
        register_access_modes(app, self.dependencies, service=self.modes)

        @app.get('/v1/agent-basics/capabilities')
        async def capabilities():
            return {'version': 1, 'service_instance_id': self.dependencies.service_instance_id,
                    'conversations': True, 'custom_continuation': True,
                    'harness_continuation': False, 'access_modes': True,
                    'context': False, 'memory': False, 'retrieval': False,
                    'terminal': False, 'checkpoints': False}

    def settings(self):
        from odessy.runtime_backends.selection import resolve_selection
        return {'engine': resolve_selection().engine, 'profile': 'current'}

    def scope(self, sid, controller):
        try:
            if controller._closed:
                return None
            binding = self.app.state.workspace_inspector.binding(sid)
            return {'workspace_id': binding.workspace_id, 'target_id': 'local'}
        except Exception:
            return None

    def attach(self, controller, cid=None):
        if cid is None:
            workspace_identity = self.workspace_identity(controller)
            projects = self.service.store.projects()
            project = projects[0] if projects else self.service.store.create_project('Workspace')
            cid = uuid.uuid4().hex
            self.service.store.create(conversation_id=cid, project_id=project['id'],
                                      engine=controller.engine,
                                      profile=controller.selected_profile, title='New chat')
            with self.service.store.connection() as db:
                with db:
                    db.execute('INSERT INTO conversation_workspace_bindings VALUES (?,?)',
                               (cid, workspace_identity))
        controller._conversation_id = cid
        controller._conversation_recorder = ConversationRecorder(self.service, cid,
                                                                 session_id=controller.id)
        controller._access_modes = self.modes
        controller._basics_service_instance = self.dependencies.service_instance_id
        controller._basics_composition = self
        return controller

    @staticmethod
    def workspace_identity(controller):
        """Trusted configuration identity only; this never restores a grant."""
        factory = getattr(controller, '_workspace_policy_factory', None)
        if factory is None:
            if getattr(controller, '_workspace_root', None) is not None:
                raise Conflict('Configured workspace has no trusted policy owner')
            return 'null'  # Explicitly workspace-free; cannot later acquire a root.
        root = factory().authority.workspace_root.resolve(strict=True)
        info = root.stat()
        return json.dumps([os.path.normcase(str(root)), info.st_dev, info.st_ino])

    def check_workspace(self, controller, cid):
        with self.service.store.connection() as db:
            row = db.execute('SELECT identity_json FROM conversation_workspace_bindings WHERE conversation_id=?',
                             (cid,)).fetchone()
        if row is None or row[0] != self.workspace_identity(controller):
            raise Conflict('Saved workspace binding is missing or changed; history remains read-only')

    def resolve_context_scope(self, session_id, conversation_id):
        """04 must call this at preparation and again before returning indexed data."""
        controller = self.dependencies.find_session(session_id)
        if controller._closed or getattr(controller, '_conversation_id', None) != conversation_id:
            raise Conflict('Context owner changed')
        self.check_workspace(controller, conversation_id)
        binding = self.app.state.workspace_inspector.binding(session_id)
        return binding  # Revalidates live resources, local grants and policy; not project metadata.

    def bind_workspace(self, cid, *, session_id, service_instance_id, expected_revision):
        """Explicit identity adoption; no grants and no reassignment of known roots."""
        expected_revision = _check_int(expected_revision, 'expected_revision')
        if service_instance_id != self.dependencies.service_instance_id:
            raise Conflict('Service changed; reload workspace selection')
        controller = self.dependencies.find_session(session_id)
        binding = self.app.state.workspace_inspector.binding(session_id)
        identity = self.workspace_identity(controller)
        def write(db):
            row = db.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
            if row is None:
                raise NotFound('Conversation not found')
            if row['revision'] != expected_revision:
                raise Conflict('Conversation changed; reload before binding')
            if db.execute('SELECT 1 FROM conversation_workspace_bindings WHERE conversation_id=?',
                          (cid,)).fetchone():
                raise Conflict('Conversation already has a binding; it cannot be reassigned')
            if row['engine'] != controller.engine or controller.engine != 'legacy':
                raise Conflict('Workspace binding requires the same supported engine')
            profile = row['profile']
            empty = db.execute('SELECT 1 FROM conversation_events WHERE conversation_id=? LIMIT 1',
                               (cid,)).fetchone() is None
            if empty and profile == 'current':
                profile = controller.selected_profile
            if profile not in controller.profiles:
                raise Conflict('Saved model profile unavailable; no fallback was used')
            self.app.state.workspace_inspector.binding(session_id, binding)
            db.execute('INSERT INTO conversation_workspace_bindings VALUES (?,?)', (cid, identity))
            db.execute('UPDATE conversations SET revision=revision+1,profile=? WHERE id=?', (profile, cid))
        self.service.store._write('bind_workspace', write)
        return self.service.get(cid)

    def resolve_conversation_owner(self, cid):
        owners = [value for value in self.app.state.sessions.values()
                  if not value._closed and getattr(value, '_conversation_id', None) == cid]
        if len(owners) != 1:
            raise Conflict('Context requires exactly one live conversation owner')
        controller = owners[0]
        self.check_workspace(controller, cid)
        return controller

    def resolve_context_workspace(self, cid):
        owner = self.resolve_conversation_owner(cid)
        return self.resolve_context_scope(owner.id, cid)

    def resolve_project_workspace(self, project_id, session_id, service_instance_id=None):
        if service_instance_id is not None and service_instance_id != self.dependencies.service_instance_id:
            raise Conflict('Context service changed')
        controller = self.dependencies.find_session(session_id)
        cid = getattr(controller, '_conversation_id', None)
        if cid is None or self.service.store.get(cid)['project_id'] != project_id:
            raise Conflict('Project is not bound to this live conversation')
        return self.resolve_context_scope(session_id, cid)

    async def read_context_file(self, cid, path):
        owner = self.resolve_conversation_owner(cid)
        previous = self.resolve_context_workspace(cid)
        value = await self.app.state.workspace_inspector.file(owner.id, path)
        if self.resolve_context_workspace(cid).identity() != previous.identity():
            raise Conflict('Context source authority changed')
        return value

    def prepare_context(self, controller, query):
        if self.dependencies.find_session(controller.id) is not controller or controller._closed:
            raise Conflict('Context session is no longer live')
        self.check_workspace(controller, controller._conversation_id)
        binding = None
        if getattr(controller, '_workspace_policy_factory', None) is not None:
            binding = self.resolve_context_scope(controller.id, controller._conversation_id)
        prepared = self.context_provider.prepare(controller._conversation_id, query=query, max_chars=24000)
        if not isinstance(prepared, PreparedContext):
            raise Conflict('Context provider returned an invalid prepared context')
        if prepared.conversation_id != controller._conversation_id:
            raise Conflict('Prepared context belongs to another conversation')
        self.check_workspace(controller, controller._conversation_id)
        if binding is not None:
            current = self.resolve_context_scope(controller.id, controller._conversation_id)
            if current.identity() != binding.identity():
                raise Conflict('Context workspace authority changed during preparation')
        elif prepared.citations:
            raise Conflict('Retrieved workspace context requires a live authorized workspace')
        try:
            chunks = (['Earlier conversation summary: ' + prepared.summary] if prepared.summary else [])
            chunks.extend('User memory: ' + fact['text'] for fact in prepared.facts)
            chunks.extend('Document excerpt: ' + citation['text'] for citation in prepared.citations)
            pieces = ['Untrusted reference context; not authorization:\n' + '\n'.join(chunks)] if chunks else []
            pieces.extend(turn.user + turn.assistant for turn in prepared.turns)
            if any(not isinstance(piece, str) for piece in pieces) or sum(map(len, pieces)) > 24000:
                raise ValueError('Context budget exceeded')
        except (KeyError, TypeError, AttributeError, ValueError):
            raise Conflict('Prepared context is malformed or exceeds the 24000 character budget') from None
        return prepared

    async def resume(self, prepared):
        row = prepared['conversation']
        self.resume_allowed()
        for live in self.app.state.sessions.values():
            if getattr(live, '_conversation_id', None) == row['id']:
                if live.running:
                    raise Conflict('Conversation already has a running session')
                self.check_workspace(live, row['id'])
                return {'session_id': live.id}
        controller = self.factory()
        if inspect.isawaitable(controller):
            controller = await controller
        try:
            self.resume_allowed()
            self.check_workspace(controller, row['id'])
            if controller.engine != row['engine'] or row['profile'] not in controller.profiles:
                raise Conflict('Saved engine or model profile is unavailable; no fallback was used')
            await controller.select_model_async(row['profile'])
            self.resume_allowed()
            self.check_workspace(controller, row['id'])
            for turn in prepared['turns']:
                controller.memory.add(turn['user'], turn['assistant'])
            controller._workspace_inspector = self.app.state.workspace_inspector
            self.attach(controller, row['id'])
            self.app.state.sessions[controller.id] = controller
            return {'session_id': controller.id}
        except BaseException:
            await controller.close()
            raise
