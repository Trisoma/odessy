"""Read-only discovery of sessions already owned by this service process."""


def register(app):
    from fastapi import HTTPException

    @app.get('/v1/sessions')
    async def list_sessions(limit: int = 50, offset: int = 0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise HTTPException(400, 'Invalid session page')
        # Copy once: pagination is live, not a durable or frozen history catalog.
        controllers = tuple(app.state.sessions.values())
        return {
            'service_instance_id': app.state.service_instance_id,
            'sessions': [
                {'id': controller.id, 'engine': controller.engine,
                 'selected_profile': controller.selected_profile,
                 'running': bool(controller.running)}
                for controller in controllers[offset:offset + limit]
            ],
            'next_offset': offset + limit if offset + limit < len(controllers) else None,
        }
